"""Speed Controller - Shared control logic for fan speed management."""

import asyncio
import logging
from collections.abc import Callable
from enum import Enum

from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from .entities.dimmer import Dimmer
from .sensors import SensorProvider, create_provider
from .util.config import CONFIG, filters_for
from .util.filters import Selection, select
from .util.tools import MedianSmoother, calculate_dimmer_value, curve_to_ranges

logger = logging.getLogger(__name__)

# Seconds to wait after the loop's own bookkeeping failed. Fixed, because the configured delay may be
# what failed.
RETRY_DELAY = 1.0


class Mode(Enum):
    """Control mode for the fan speed."""

    AUTO = "auto"
    MANUAL = "manual"


class SpeedController:
    """
    Manages fan speed control with auto and manual modes.

    In AUTO mode, fan speed follows the hottest reading the source's filters match.
    In MANUAL mode, fan speed is set directly by the user.
    """

    def __init__(self, sensor_provider: SensorProvider | None = None):
        self.device = Dimmer()
        self._sensors = sensor_provider or create_provider(CONFIG.sensors.provider)
        self._mode = Mode(CONFIG.control.mode)  # validated on load, so this cannot raise here
        self._manual_speed = CONFIG.control.manual_speed
        self._running = False
        self._loop_task: asyncio.Task | None = None

        # Status callbacks
        self._on_status_change: Callable | None = None
        self._on_temps_change: Callable | None = None
        self._on_speed_change: Callable | None = None

        # Current status
        # The smoothed maximum drives the curve; the selection says which filter and sensor gave it.
        self._max_temp = 0
        self._selection = Selection()
        self._current_speed = 0
        self._connected = False
        # Whether the temperature source answered at all. A provider that cannot reach its app
        # returns no readings rather than raising, which would otherwise look like a cold machine.
        self._sensors_ok = True
        # Whether the previous tick raised, so a lasting fault is reported once rather than every tick.
        self._tick_failing = False

        # The value we last wrote is the source of truth; the device is only re-read on
        # (re)connect and every RESYNC_EVERY ticks, to catch changes made outside this app.
        self._last_sent: int | None = None
        self._ticks_since_resync = 0

        self._temp_window = max(1, int(CONFIG.control.temp_window))
        self._smoother = MedianSmoother(self._temp_window)

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
        """Whether the last read got any temperatures out of the source."""
        return self._sensors_ok

    def _set_sensors_ok(self, value: bool):
        """Record whether the source answered, announcing only the transitions."""
        if value == self._sensors_ok:
            return

        self._sensors_ok = value
        name = getattr(self.sensors, "name", type(self.sensors).__name__)
        if value:
            logger.info(f"Temperature source {name} is answering again")
        else:
            logger.error(f"No temperatures from {name}: the fan is running on the curve's floor")
        self._notify_status()

    @property
    def max_temp(self) -> int:
        """The smoothed hottest reading the filters match: what the curve is evaluated at."""
        return self._max_temp

    @property
    def selection(self) -> Selection:
        """Every filter's latest raw match, and which one holds the maximum."""
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
    def sensors(self) -> SensorProvider:
        """The temperature source in use."""
        return self._sensors

    @sensors.setter
    def sensors(self, provider):
        """Swap the temperature source, by name or by instance.

        Labels differ between sources, so readings taken through the old one say nothing about the
        new one: the smoothers start over. Published with a single assignment, because the control
        loop reads this attribute from the asyncio thread while the GUI writes it from Tk.
        """
        provider = create_provider(provider) if isinstance(provider, str) else provider
        if provider is self._sensors:
            return

        self._sensors = provider
        self._reset_smoothers()
        self._sensors_ok = True  # the new source has not failed yet; do not inherit the old verdict
        logger.info(f"Sensor source is now {getattr(provider, 'name', type(provider).__name__)}")

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
        return f"{hottest.label} via {hottest.pattern!r}" if hottest else "no filter matched"

    def _reset_smoothers(self):
        """A fresh smoother sized to the current window, published with a single assignment."""
        self._smoother = MedianSmoother(self._temp_window)

    @property
    def port(self) -> str | None:
        """Current serial port."""
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
            self._on_status_change(self._connected, self._running, self._sensors_ok)

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

        # Set fan to 0 when stopping
        await self._set_fan_speed(0)
        self._notify_status()
        logger.info("Control loop stopped")

    async def _set_fan_speed(self, value: int):
        """Set the fan speed on the device."""
        if self.device.connected:
            await self.device.set_dimmer_value(value)
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
        reported = await self.device.read_dimmer_value()
        if reported is None:
            return self._last_sent

        if self._last_sent is not None and reported != self._last_sent:
            logger.warning(f"{CONFIG.device.pwm_command} changed outside this app: {self._last_sent} -> {reported}")

        self._last_sent = reported
        return reported

    async def _connect(self) -> bool:
        """Attempt to connect to the device."""
        if self.device.connected:
            return True

        # Try direct connection first
        await self.device.connect()
        if self.device.connected:
            self._connected = True
            self._notify_status()
            return True

        # Try to find device by name or serial
        coms: list[ListPortInfo] = comports()
        coms_match = []

        if CONFIG.device.name:
            coms_match = [_ for _ in coms if CONFIG.device.name in _.description]

        if CONFIG.device.serial:
            coms_match = [_ for _ in coms if _.serial_number and CONFIG.device.serial in _.serial_number] or coms_match

        if coms_match:
            com = coms_match[0]
            self.device.port = com.device
            logger.info(f"Serial Device found at {self.device.port}")
            await self.device.connect()

        self._connected = self.device.connected
        self._notify_status()
        return self._connected

    async def reconnect(self) -> bool:
        """Close the port and open it again from the current device configuration.

        Port, baudrate, timeout and the PWM command name are read when the Dimmer is built, so a
        change to any of them needs a new device rather than a new connection. This runs on the
        asyncio thread: call it from the GUI with asyncio.run_coroutine_threadsafe.
        """
        if self.device.connected:
            await self.device.disconnect()

        self.device = Dimmer()
        # Whatever the previous device reported says nothing about this one.
        self._last_sent = None
        self._ticks_since_resync = 0
        self._connected = False
        self._notify_status()

        logger.info(f"Reconnecting to {self.device.port} at {self.device.baudrate} baud")
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
                except Exception:
                    logger.exception(f"Error in the control loop itself; retrying in {RETRY_DELAY} s")
                    await asyncio.sleep(RETRY_DELAY)

        except asyncio.CancelledError:
            logger.debug("Control loop cancelled")
            raise

    async def _run_tick(self):
        """Run one tick and report how it went."""
        try:
            drove_the_fan = await self._tick()
        except Exception as e:  # noqa: BLE001 -- whatever a tick raises, the next tick must still run
            self._tick_failed(e)
            return

        # A tick that skipped its work (no device, no temperatures) is not a recovery: those cases
        # report themselves through the connection and sensor status.
        if drove_the_fan:
            self._tick_succeeded()

    def _tick_failed(self, error: Exception):
        """Report a failing tick once with its traceback, not once a second for as long as it fails."""
        was_connected = self._connected
        self._connected = False
        # After an error the device state is unknown, so the next good tick re-reads it.
        self._last_sent = None
        self._ticks_since_resync = 0

        if not self._tick_failing:
            self._tick_failing = True
            logger.error("Error in control loop; skipping this tick and trying again", exc_info=error)
            self._notify_status()
            return

        logger.debug(f"Control tick failed again: {error!r}")
        if was_connected:
            # Something marked the link up meanwhile, a Reconnect from Settings for one; say it is down.
            self._notify_status()

    def _tick_succeeded(self):
        if self._tick_failing:
            self._tick_failing = False
            self._connected = True
            logger.info("Control loop is working again")
            self._notify_status()  # the failure turned the tray red; this turns it back

    async def _tick(self) -> bool:
        """Read the temperatures once and bring the fan to the speed they call for.

        Returns whether it got as far as driving the fan.
        """
        # Attempt connection if not connected
        if not self.device.connected:
            await self._connect()

        if not self.device.connected:
            self._connected = False
            # Forget the cached value so the next connection re-reads the real one.
            self._last_sent = None
            self._ticks_since_resync = 0
            self._notify_status()
            return False

        # While ticks keep failing the link stays reported down: marking it up here, before the tick
        # fails again, would show Settings a connection that drives nothing.
        if not self._tick_failing:
            self._connected = True

        # Read temperatures. WMI is blocking, so keep it off the event loop.
        try:
            sensors = await asyncio.to_thread(self.sensors.get_temperatures)
        except Exception as e:  # noqa: BLE001 -- WMI/COM raise arbitrary types; a source error is not a loop error
            logger.error(f"Error reading sensors: {e}")
            self._set_sensors_ok(False)
            return False

        # An empty result is how every provider reports "I cannot reach my app".
        self._set_sensors_ok(bool(sensors))

        self._selection = select(sensors, filters_for(CONFIG, self.sensors.name))
        # Nothing matched reads as 0 °C, which holds the fan at the curve's floor; Settings shows which
        # filters match nothing, so that state is visible rather than silent.
        raw = self._selection.max_value or 0

        # Round rather than truncate: sources such as the LibreHardwareMonitor web
        # server report fractions, and int() would bias every reading downwards.
        self._max_temp = round(self._smoother.add(raw))
        if raw != self._max_temp:
            logger.debug(f"Smoothed max {raw} -> {self._max_temp} (median of {self._temp_window})")
        self._notify_temps()

        current_dimmer = await self._current_dimmer()

        # Calculate new speed based on mode
        if self._mode == Mode.AUTO:
            ranges = self._ranges  # read once: the GUI may swap it mid-tick
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
            logger.info(
                f"Max: {self._max_temp} ({self._describe_hottest()}). "
                f"{CONFIG.device.pwm_command}: {current_dimmer} -> {new_value}"
            )
            await self._set_fan_speed(new_value)
        elif current_dimmer is not None:
            self._current_speed = current_dimmer
            self._notify_speed()

        return True

    async def shutdown(self):
        """Shutdown the controller gracefully."""
        await self.stop()
        if self.device.connected:
            await self.device.disconnect()
        self._connected = False
        self._notify_status()
