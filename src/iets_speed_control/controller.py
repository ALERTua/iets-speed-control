"""Speed Controller - Shared control logic for fan speed management."""

import asyncio
import logging
from collections.abc import Callable
from enum import Enum

from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from .entities.dimmer import Dimmer
from .sensors import SensorProvider, create_provider
from .util import env
from .util.tools import MedianSmoother, calculate_dimmer_value

logger = logging.getLogger(__name__)


class Mode(Enum):
    """Control mode for the fan speed."""

    AUTO = "auto"
    MANUAL = "manual"


class SpeedController:
    """
    Manages fan speed control with auto and manual modes.

    In AUTO mode, fan speed is calculated from CPU/GPU temperatures.
    In MANUAL mode, fan speed is set directly by the user.
    """

    def __init__(self, sensor_provider: SensorProvider | None = None):
        self.device = Dimmer()
        self.sensors = sensor_provider or create_provider(env.SENSOR_PROVIDER)
        self._mode = Mode.AUTO
        self._manual_speed = 0
        self._running = False
        self._loop_task: asyncio.Task | None = None

        # Status callbacks
        self._on_status_change: Callable | None = None
        self._on_temps_change: Callable | None = None
        self._on_speed_change: Callable | None = None

        # Current status
        self._cpu_temp = 0
        self._gpu_temp = 0
        self._current_speed = 0
        self._connected = False

        # The value we last wrote is the source of truth; the device is only re-read on
        # (re)connect and every RESYNC_EVERY ticks, to catch changes made outside this app.
        self._last_sent: int | None = None
        self._ticks_since_resync = 0

        self._cpu_smoother = MedianSmoother(env.TEMP_WINDOW)
        self._gpu_smoother = MedianSmoother(env.TEMP_WINDOW)

    @property
    def mode(self) -> Mode:
        """Current control mode."""
        return self._mode

    @mode.setter
    def mode(self, value: Mode):
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
    def cpu_temp(self) -> int:
        """Current CPU temperature."""
        return self._cpu_temp

    @property
    def gpu_temp(self) -> int:
        """Current GPU temperature."""
        return self._gpu_temp

    @property
    def current_speed(self) -> int:
        """Current fan speed."""
        return self._current_speed

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
            self._on_status_change(self._connected, self._running)

    def _notify_temps(self):
        """Notify temperature change callback."""
        if self._on_temps_change:
            self._on_temps_change(self._cpu_temp, self._gpu_temp)

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
        due = env.RESYNC_EVERY and self._ticks_since_resync >= env.RESYNC_EVERY
        if self._last_sent is not None and not due:
            self._ticks_since_resync += 1
            return self._last_sent

        self._ticks_since_resync = 0
        reported = await self.device.read_dimmer_value()
        if reported is None:
            return self._last_sent

        if self._last_sent is not None and reported != self._last_sent:
            logger.warning(f"{env.PWM_COMMAND} changed outside this app: {self._last_sent} -> {reported}")

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

        if env.DEVICE_NAME:
            coms_match = [_ for _ in coms if env.DEVICE_NAME in _.description]

        if env.DEVICE_SERIAL:
            coms_match = [_ for _ in coms if _.serial_number and env.DEVICE_SERIAL in _.serial_number] or coms_match

        if coms_match:
            com = coms_match[0]
            self.device.port = com.device
            logger.info(f"Serial Device found at {self.device.port}")
            await self.device.connect()

        self._connected = self.device.connected
        self._notify_status()
        return self._connected

    async def _control_loop(self):
        """Main control loop."""
        try:
            while self._running:
                # Attempt connection if not connected
                if not self.device.connected:
                    await self._connect()

                if self.device.connected:
                    self._connected = True

                    # Read temperatures. WMI is blocking, so keep it off the event loop.
                    try:
                        sensors = await asyncio.to_thread(self.sensors.get_temperatures)
                    except Exception as e:  # noqa: BLE001 -- WMI/COM raise arbitrary types; never kill the loop
                        logger.error(f"Error reading sensors: {e}")
                        await asyncio.sleep(env.DELAY)
                        continue

                    cpu_temps = [v for k, v in sensors.items() if env.CPU_SENSOR_FILTER in k]
                    gpu_temps = [v for k, v in sensors.items() if env.GPU_SENSOR_FILTER in k]

                    # Round rather than truncate: sources such as the LibreHardwareMonitor web
                    # server report fractions, and int() would bias every reading downwards.
                    raw_cpu = max(cpu_temps or [0])
                    raw_gpu = max(gpu_temps or [0])
                    self._cpu_temp = round(self._cpu_smoother.add(raw_cpu))
                    self._gpu_temp = round(self._gpu_smoother.add(raw_gpu))
                    if (raw_cpu, raw_gpu) != (self._cpu_temp, self._gpu_temp):
                        logger.debug(
                            f"Smoothed CPU {raw_cpu} -> {self._cpu_temp}, GPU {raw_gpu} -> {self._gpu_temp}"
                            f" (median of {env.TEMP_WINDOW})"
                        )
                    self._notify_temps()

                    current_dimmer = await self._current_dimmer()

                    # Calculate new speed based on mode
                    if self._mode == Mode.AUTO:
                        cpu_dimmer = calculate_dimmer_value(self._cpu_temp, env.TEMP_RANGES)
                        gpu_dimmer = calculate_dimmer_value(self._gpu_temp, env.TEMP_RANGES)
                        new_value = max(cpu_dimmer, gpu_dimmer)

                        # Apply step limits
                        if current_dimmer is not None and env.MAX_STEP:
                            new_value = max(new_value, current_dimmer - env.MAX_STEP)

                        # Apply minimum change threshold
                        if current_dimmer is not None and abs(current_dimmer - new_value) < env.IGNORE_LESS_THAN:
                            new_value = current_dimmer
                    else:
                        # Manual mode
                        new_value = self._manual_speed

                    # Update speed if changed
                    if current_dimmer != new_value:
                        logger.info(
                            f"CPU: {self._cpu_temp}, GPU: {self._gpu_temp}. "
                            f"{env.PWM_COMMAND}: {current_dimmer} -> {new_value}"
                        )
                        await self._set_fan_speed(new_value)
                    elif current_dimmer is not None:
                        self._current_speed = current_dimmer
                        self._notify_speed()
                else:
                    self._connected = False
                    # Forget the cached value so the next connection re-reads the real one.
                    self._last_sent = None
                    self._ticks_since_resync = 0
                    self._notify_status()

                await asyncio.sleep(env.DELAY)

        except asyncio.CancelledError:
            logger.debug("Control loop cancelled")
            raise
        except Exception:
            # Last-resort guard: log with the traceback rather than let the loop die silently.
            logger.exception("Error in control loop")
            self._connected = False
            self._notify_status()

    async def shutdown(self):
        """Shutdown the controller gracefully."""
        await self.stop()
        if self.device.connected:
            await self.device.disconnect()
        self._connected = False
        self._notify_status()
