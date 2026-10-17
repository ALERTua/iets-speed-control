"""Speed Controller - Shared control logic for fan speed management."""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from enum import Enum

from .entities.fan import FanDevice
from .entities.tasmota_fan import create_fan
from .sensors import SensorProvider, create_provider, lacks_admin_rights
from .sensors.base import plausible
from .util.config import CONFIG
from .util.tools import MedianSmoother, calculate_dimmer_value, curve_to_ranges
from .util.tracking import Selection, SourceState, select, tracked_count

logger = logging.getLogger(__name__)

# Seconds to wait after the loop's own bookkeeping failed. Fixed, because the configured delay may be
# what failed.
RETRY_DELAY = 1.0

# Seconds between two ticks that mean the machine slept: far beyond any sensible tick delay, and fixed so a
# broken delay cannot break this check too. A serial handle can come out of hibernation looking open while
# the driver no longer delivers anything through it.
SLEEP_GAP = 60.0


class Mode(Enum):
    """Control mode for the fan speed."""

    AUTO = "auto"
    MANUAL = "manual"


class SpeedController:
    """
    Manages fan speed control with auto and manual modes.

    In AUTO mode, fan speed follows the hottest tracked sensor, from whichever sources they come from,
    and falls back to control.default_speed when none of them has a reading.
    In MANUAL mode, fan speed is set directly by the user.
    """

    def __init__(self, sources: Mapping[str, SensorProvider] | None = None):
        """`sources` stands in for the sources of those code names instead of creating them; tests use it."""
        self.device: FanDevice = create_fan()
        self._given_sources = dict(sources or {})
        self._sources: dict[str, SensorProvider] = {}
        self._mode = Mode(CONFIG.control.mode)  # validated on load, so this cannot raise here
        self._manual_speed = CONFIG.control.manual_speed
        self._running = False
        self._loop_task: asyncio.Task | None = None

        # Status callbacks
        self._on_status_change: Callable | None = None
        self._on_temps_change: Callable | None = None
        self._on_speed_change: Callable | None = None

        # Current status
        # The smoothed maximum drives the curve; the selection says which tracked sensor gave it.
        self._max_temp = 0
        self._selection = Selection()
        # The latest plausible readings and the state of each source, both replaced whole every tick.
        self._readings: dict[str, dict[str, float]] = {}
        self._source_states: dict[str, SourceState] = {}
        # Whether the last tick had nothing to go on, so the fan ran at control.default_speed.
        self._fallback = False
        self._current_speed = 0
        self._connected = False
        # Whether every tracked source answered. A provider that cannot reach its app returns no readings
        # rather than raising, which would otherwise look like a cold machine.
        self._sensors_ok = True
        # The silent sources with their states, as last announced: a change of it redraws the status line.
        self._silence: tuple[tuple[str, SourceState], ...] = ()
        # Whether the previous tick raised, so a lasting fault is reported once rather than every tick.
        self._tick_failing = False
        # The same for a fault outside the tick, in the loop's own reporting or waiting.
        self._iteration_failing = False

        # The value we last wrote is the source of truth; the device is only re-read on
        # (re)connect and every RESYNC_EVERY ticks, to catch changes made outside this app.
        self._last_sent: int | None = None
        self._ticks_since_resync = 0
        # Wall-clock time of the previous tick. Wall clock rather than monotonic: only the wall clock is
        # sure to count the time the machine spent asleep.
        self._last_tick_at: float | None = None

        self._temp_window = max(1, int(CONFIG.control.temp_window))
        self._smoother = MedianSmoother(self._temp_window)
        self.sync_sources()

        # The curve is editable at runtime from the GUI. Configuration only seeds it.
        self._curve = [(float(temperature), float(percent)) for temperature, percent in CONFIG.control.curve]
        self._ranges = curve_to_ranges(self._curve)

    @property
    def mode(self) -> Mode:
        """Current control mode."""
        return self._mode

    @mode.setter
    def mode(self, value: Mode):
        # Kept in the configuration as well, so Save writes it and the next run starts in the mode
        # this one was left in.
        CONFIG.control.mode = value.value
        if self._mode != value:
            self._mode = value
            logger.info(f"Mode changed to {value.name}")

    @property
    def manual_speed(self) -> int:
        """Manual fan speed (0-100)."""
        return self._manual_speed

    @manual_speed.setter
    def manual_speed(self, value: int):
        self._manual_speed = max(0, min(100, int(value)))
        # Kept in the configuration too, so Save writes it and the next run starts where this one
        # left off rather than at 0 %.
        CONFIG.control.manual_speed = self._manual_speed
        logger.debug(f"Manual speed set to {self._manual_speed}")

    @property
    def running(self) -> bool:
        """Whether the control loop is running."""
        return self._running

    @property
    def connected(self) -> bool:
        """Whether the device is connected."""
        return self._connected

    @property
    def sensors_ok(self) -> bool:
        """Whether every tracked source answered its last read. A sleeping GPU counts as answering."""
        return self._sensors_ok

    def _update_silence(self):
        """Record which sources gave nothing, announcing each change of that set rather than every tick.

        The set, not only whether it is empty: Home names each silent source and its reason, so a source
        that went quiet, recovered, needs other rights or was removed while another stays silent must
        reach the status line too.
        """
        silence = tuple((name, self._source_states[name]) for name in self.silent_sources)
        if silence == self._silence:
            return

        self._silence = silence
        self._sensors_ok = not silence
        if silence:
            logger.error(f"No temperatures from {', '.join(name for name, _state in silence)}")
        else:
            logger.info("Every temperature source is answering again")
        self._notify_status()

    @property
    def silent_sources(self) -> list[str]:
        """The tracked sources that gave nothing on the last read, for a reason other than sleep."""
        silent = (SourceState.SILENT, SourceState.NEEDS_ADMIN)
        return [name for name, state in self._source_states.items() if state in silent]

    @property
    def source_states(self) -> dict[str, SourceState]:
        """How each source answered its last read."""
        return dict(self._source_states)

    @property
    def readings(self) -> dict[str, dict[str, float]]:
        """The latest plausible readings of each source, tracked or not."""
        return self._readings

    @property
    def monitoring(self) -> bool:
        """Whether any sensor is tracked at all. Without one the fan runs at control.default_speed."""
        return tracked_count(CONFIG.sensors.tracked) > 0

    @property
    def fallback(self) -> bool:
        """Whether the last tick had no reading to go on, so Auto mode ran the fan at control.default_speed."""
        return self._fallback

    @property
    def max_temp(self) -> int:
        """The smoothed hottest tracked reading: what the curve is evaluated at."""
        return self._max_temp

    @property
    def selection(self) -> Selection:
        """Every tracked sensor's latest raw value, and which one holds the maximum."""
        return self._selection

    @property
    def current_speed(self) -> int:
        """Current fan speed."""
        return self._current_speed

    @property
    def curve(self) -> list[tuple[float, float]]:
        """Fan curve as [(temperature, percent), ...]."""
        return list(self._curve)

    @curve.setter
    def curve(self, points):
        """Replace the curve; takes effect on the next tick.

        The GUI runs on the Tk thread while the control loop runs on the asyncio thread, so the
        new ranges are built first and published with a single attribute assignment. A tick either
        sees the whole old curve or the whole new one.
        """
        ordered = sorted((float(t), float(p)) for t, p in points)
        if len(ordered) < 2:
            raise ValueError("a curve needs at least two points")

        self._ranges = curve_to_ranges(ordered)
        self._curve = ordered
        logger.debug(f"Curve replaced: {ordered}")

    @property
    def sources(self) -> dict[str, SensorProvider]:
        """The sources being read, by code name: every source in sensors.tracked."""
        return dict(self._sources)

    def sync_sources(self):
        """Read the sources sensors.tracked names now, keeping each one that stays.

        A source keeps its instance, so its own state (a logged warning, a found GPU) survives an edit.
        Published with a single assignment, because the control loop reads it from the asyncio thread
        while the GUI calls this from Tk. Unknown names never get here: the configuration rejects them.
        """
        wanted = list(CONFIG.sensors.tracked)
        if wanted != list(self._sources):
            current = self._sources
            self._sources = {
                name: current.get(name) or self._given_sources.get(name) or create_provider(name) for name in wanted
            }
            # What the old set read says nothing about the new one: start the smoothing over, and drop the
            # readings and the verdict of sources that are gone.
            self._reset_smoothers()
            self._source_states = {name: state for name, state in self._source_states.items() if name in self._sources}
            self._readings = {name: readings for name, readings in self._readings.items() if name in self._sources}
            logger.info(f"Reading {', '.join(wanted) or 'no source'}")

        # An edit shows at once, without waiting for a tick that a stopped loop never runs: Home drops the
        # rows and the status of what is no longer tracked.
        self._selection = select(self._readings, CONFIG.sensors.tracked)
        self._update_silence()
        self._notify_temps()

    @property
    def temp_window(self) -> int:
        """Number of readings the rolling median covers."""
        return self._temp_window

    @temp_window.setter
    def temp_window(self, size: int):
        """Resize the smoothing window, which means replacing the smoothers."""
        size = max(1, int(size))
        if size == self._temp_window:
            return

        self._temp_window = size
        self._reset_smoothers()
        logger.debug(f"Smoothing window is now {size}")

    def _describe_hottest(self) -> str:
        hottest = self._selection.hottest
        return f"{hottest.source}/{hottest.label}" if hottest else "no tracked reading"

    def _reset_smoothers(self):
        """A fresh smoother sized to the current window, published with a single assignment."""
        self._smoother = MedianSmoother(self._temp_window)

    @property
    def port(self) -> str | None:
        """Where the fan device is reached, as shown to the user."""
        return self.device.port

    def set_callbacks(
        self,
        on_status_change: Callable | None = None,
        on_temps_change: Callable | None = None,
        on_speed_change: Callable | None = None,
    ):
        """Set callback functions for status updates."""
        self._on_status_change = on_status_change
        self._on_temps_change = on_temps_change
        self._on_speed_change = on_speed_change

    def _notify_status(self):
        """Notify status change callback."""
        if self._on_status_change:
            self._on_status_change(self._connected, self._running, self._sensors_ok, self.loop_ok)

    def _notify_temps(self):
        """Notify temperature change callback."""
        if self._on_temps_change:
            self._on_temps_change(self._max_temp, self._selection)

    def _notify_speed(self):
        """Notify speed change callback."""
        if self._on_speed_change:
            self._on_speed_change(self._current_speed)

    async def start(self):
        """Start the control loop."""
        if self._running:
            return

        self._running = True
        self._loop_task = asyncio.create_task(self._control_loop())
        self._notify_status()
        logger.info("Control loop started")

    async def stop(self):
        """Stop the control loop."""
        if not self._running:
            return

        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        # Nothing watches the temperatures any more, so the fan gets the speed that is safe without them.
        await self._set_fan_speed(CONFIG.control.default_speed)
        self._notify_status()
        logger.info("Control loop stopped")

    async def _set_fan_speed(self, value: int):
        """Set the fan speed on the device."""
        if self.device.connected:
            await self.device.set_speed(value)
            self._last_sent = value
            self._current_speed = value
            self._notify_speed()

    async def _current_dimmer(self) -> int | None:
        """Return the device's dimmer value, reading it over serial only when necessary.

        Re-reading every tick doubles the serial traffic and costs a read timeout each time, so the
        value we last wrote is reused instead. The device is polled on (re)connect, when we have no
        value yet, and every RESYNC_EVERY ticks so an external change (Tasmota web UI, a reboot) is
        still picked up.
        """
        due = CONFIG.control.resync_every and self._ticks_since_resync >= CONFIG.control.resync_every
        if self._last_sent is not None and not due:
            self._ticks_since_resync += 1
            return self._last_sent

        self._ticks_since_resync = 0
        reported = await self.device.read_speed()
        if reported is None:
            return self._last_sent

        if self._last_sent is not None and reported != self._last_sent:
            logger.warning(f"Fan speed changed outside this app: {self._last_sent} -> {reported}")

        self._last_sent = reported
        return reported

    async def _connect(self) -> bool:
        """Open the fan device; finding it, when it moved, is the device's own business."""
        if self.device.connected:
            return True

        await self.device.connect()
        self._connected = self.device.connected
        self._notify_status()
        return self._connected

    async def reconnect(self) -> bool:
        """Close the port and open it again from the current device configuration.

        The device reads its settings (for Tasmota: port, baudrate, timeout, PWM command) when it is
        built, so a change to any of them needs a new device rather than a new connection. This runs
        on the asyncio thread: call it from the GUI with asyncio.run_coroutine_threadsafe.
        """
        if self.device.connected:
            await self.device.disconnect()

        self.device = create_fan()
        # Whatever the previous device reported says nothing about this one.
        self._last_sent = None
        self._ticks_since_resync = 0
        self._connected = False
        self._notify_status()

        logger.info(f"Reconnecting to {self.device.describe()}")
        return await self._connect()

    async def _control_loop(self):
        """Run one tick every control.delay until stopped.

        An unexpected error costs the tick it happened in, never the loop. A loop that ended here
        would leave the fan at whatever speed it last had, with `running` still true so that nothing
        could start it again short of restarting the app.
        """
        try:
            while self._running:
                # Everything an iteration does sits under this guard, the reporting and the wait
                # included: the status callback runs GUI code, and nothing may end the loop but stop().
                try:
                    await self._run_tick()
                    await asyncio.sleep(CONFIG.control.delay)
                except Exception as e:  # noqa: BLE001 -- the last line of defence for the loop itself
                    self._iteration_failed(e)
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    self._iteration_succeeded()

        except asyncio.CancelledError:
            logger.debug("Control loop cancelled")
            raise

    def _iteration_failed(self, error: Exception):
        """Report a fault outside the tick once with its traceback, like a tick fault."""
        if self._iteration_failing:
            logger.debug(f"Control loop failed again: {error!r}")
            return

        self._iteration_failing = True
        logger.error(f"Error in the control loop itself; retrying every {RETRY_DELAY} s", exc_info=error)

    def _iteration_succeeded(self):
        if self._iteration_failing:
            self._iteration_failing = False
            logger.info("Control loop itself is working again")

    async def _run_tick(self):
        """Run one tick and report how it went."""
        try:
            await self._tick()
        except Exception as e:  # noqa: BLE001 -- whatever a tick raises, the next tick must still run
            self._tick_failed(e)
        else:
            self._tick_succeeded()

    @property
    def loop_ok(self) -> bool:
        """Whether the last tick ran without an unexpected error."""
        return not self._tick_failing

    def _tick_failed(self, error: Exception):
        """Report a failing tick once with its traceback, not once a second for as long as it fails.

        The link state stays what the device says: a fault in the sensors or in the GUI says nothing
        about the serial port, and the status carries the fault on its own.
        """
        if self._tick_failing:
            logger.debug(f"Control tick failed again: {error!r}")
            return

        self._tick_failing = True
        logger.error("Error in control loop; skipping this tick and trying again", exc_info=error)
        self._notify_status()

    def _tick_succeeded(self):
        """A tick that ran to its end without an unexpected error ends a fault.

        It may still have skipped its work, for a source with no temperatures for one; that is reported
        through the sensor status, not as a loop fault.
        """
        if self._tick_failing:
            self._tick_failing = False
            logger.info("Control loop is working again")
            self._notify_status()  # the failure turned the tray red; this turns it back

    def _forget_device_value(self):
        """Drop the cached dimmer value so the next exchange reads the real one."""
        self._last_sent = None
        self._ticks_since_resync = 0

    def _slept_since_last_tick(self) -> bool:
        now = time.time()
        previous, self._last_tick_at = self._last_tick_at, now
        return previous is not None and now - previous > SLEEP_GAP

    async def _tick(self):
        """Read the temperatures once and bring the fan to the speed they call for."""
        if self._slept_since_last_tick() and self.device.connected:
            logger.info(f"The machine slept; reopening {self.device.describe()}")
            await self.device.disconnect()

        # Attempt connection if not connected
        if not self.device.connected:
            # Whatever was last written may never have reached a device that dropped off: read it again.
            self._forget_device_value()
            await self._connect()

        if not self.device.connected:
            self._connected = False
            # Forget the cached value so the next connection re-reads the real one.
            self._forget_device_value()
            self._notify_status()
            return

        self._connected = True

        # Every source at once, each in a worker thread: WMI and HTTP block, and one slow source must not
        # add its wait to the others'.
        sources = self._sources  # read once: the GUI may replace it mid-tick
        answers = await asyncio.gather(*(asyncio.to_thread(read_source, provider) for provider in sources.values()))
        self._readings = {name: readings for name, (_state, readings) in zip(sources, answers, strict=True)}
        self._source_states = {name: state for name, (state, _readings) in zip(sources, answers, strict=True)}
        self._update_silence()

        tracked = CONFIG.sensors.tracked
        self._selection = select(self._readings, tracked)
        raw = self._selection.max_value
        if raw is None and self._every_tracked_source_sleeps(tracked):
            raw = 0  # a sleeping GPU is cold, so the curve's floor is right for it; nothing is wrong

        self._fallback = raw is None
        if raw is not None:
            # Round rather than truncate: sources such as the LibreHardwareMonitor web
            # server report fractions, and int() would bias every reading downwards.
            self._max_temp = round(self._smoother.add(raw))
            if raw != self._max_temp:
                logger.debug(f"Smoothed max {raw} -> {self._max_temp} (median of {self._temp_window})")
        self._notify_temps()

        try:
            await self._drive_fan()
        except Exception:
            # The device was in the middle of an exchange, so its state is unknown: the next tick reads
            # it again. A fault anywhere else keeps the cached value and spares the serial round trip.
            self._forget_device_value()
            raise

    async def _drive_fan(self):
        """Bring the fan to the speed the current temperatures, or Manual mode, call for."""
        current_dimmer = await self._current_dimmer()

        # Calculate new speed based on mode
        if self._mode == Mode.AUTO:
            ranges = self._ranges  # read once: the GUI may swap it mid-tick
            if self._fallback:
                new_value = CONFIG.control.default_speed
            else:
                new_value = calculate_dimmer_value(self._max_temp, ranges)

            # Apply step limits
            if current_dimmer is not None and CONFIG.control.max_step:
                new_value = max(new_value, current_dimmer - CONFIG.control.max_step)

            # Apply minimum change threshold
            if current_dimmer is not None and abs(current_dimmer - new_value) < CONFIG.control.ignore_less_than:
                new_value = current_dimmer
        else:
            # Manual mode
            new_value = self._manual_speed

        # Update speed if changed
        if current_dimmer != new_value:
            reason = "No tracked reading, default speed" if self._fallback else f"Max: {self._max_temp}"
            logger.info(
                f"{reason} ({self._describe_hottest()}). {CONFIG.device.pwm_command}: {current_dimmer} -> {new_value}"
            )
            await self._set_fan_speed(new_value)
        elif current_dimmer is not None:
            self._current_speed = current_dimmer
            self._notify_speed()

    def _every_tracked_source_sleeps(self, tracked) -> bool:
        """Whether each source with a tracked sensor is asleep, which is why none of them has a reading."""
        busy = [name for name, labels in tracked.items() if labels]
        return bool(busy) and all(self._source_states.get(name) is SourceState.ASLEEP for name in busy)

    async def shutdown(self):
        """Shutdown the controller gracefully, leaving the fan at control.default_speed."""
        if self._running:
            await self.stop()
        else:
            # stop() does nothing for a loop that is not running, but the app's exit still must not leave
            # the fan at whatever speed it last had, nor at one this app set by hand.
            await self._set_fan_speed(CONFIG.control.default_speed)
        if self.device.connected:
            await self.device.disconnect()
        self._connected = False
        self._notify_status()


def read_source(provider: SensorProvider) -> tuple[SourceState, dict[str, float]]:
    """Read one source and say how it answered. Blocking, and never raises: one source's fault is not the loop's."""
    try:
        readings = plausible(provider.get_temperatures())
    except Exception as e:  # noqa: BLE001 -- WMI/COM raise arbitrary types; a source error is not a loop error
        logger.error(f"Error reading {getattr(provider, 'name', type(provider).__name__)}: {e}")
        return SourceState.SILENT, {}

    if readings:
        return SourceState.OK, readings
    if lacks_admin_rights(provider):
        return SourceState.NEEDS_ADMIN, {}
    if getattr(provider, "asleep", False):
        return SourceState.ASLEEP, {}
    # An empty result is how every source reports "I cannot reach my app".
    return SourceState.SILENT, {}
