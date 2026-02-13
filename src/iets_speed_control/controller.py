"""Speed Controller - Shared control logic for fan speed management."""

import asyncio
import logging
from enum import Enum
from typing import Optional, Callable
from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from .entities.dimmer import Dimmer
from .util import env
from .util.sensors import get_sensors
from .util.tools import calculate_dimmer_value


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

    def __init__(self):
        self.device = Dimmer()
        self._mode = Mode.AUTO
        self._manual_speed = 0
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None

        # Status callbacks
        self._on_status_change: Optional[Callable] = None
        self._on_temps_change: Optional[Callable] = None
        self._on_speed_change: Optional[Callable] = None

        # Current status
        self._cpu_temp = 0
        self._gpu_temp = 0
        self._current_speed = 0
        self._connected = False

    @property
    def mode(self) -> Mode:
        """Current control mode."""
        return self._mode

    @mode.setter
    def mode(self, value: Mode):
        if self._mode != value:
            self._mode = value
            logging.info(f"Mode changed to {value.name}")

    @property
    def manual_speed(self) -> int:
        """Manual fan speed (0-100)."""
        return self._manual_speed

    @manual_speed.setter
    def manual_speed(self, value: int):
        self._manual_speed = max(0, min(100, int(value)))
        logging.debug(f"Manual speed set to {self._manual_speed}")

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
    def port(self) -> Optional[str]:
        """Current serial port."""
        return self.device.port

    def set_callbacks(
        self,
        on_status_change: Optional[Callable] = None,
        on_temps_change: Optional[Callable] = None,
        on_speed_change: Optional[Callable] = None,
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
        logging.info("Control loop started")

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
        logging.info("Control loop stopped")

    async def _set_fan_speed(self, value: int):
        """Set the fan speed on the device."""
        if self.device.connected:
            await self.device.set_dimmer_value(value)
            self._current_speed = value
            self._notify_speed()

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
            logging.info(f"Serial Device found at {self.device.port}")
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

                    # Read temperatures
                    sensors = get_sensors()
                    cpu_temps = {k: int(v) for k, v in sensors.items() if env.CPU_SENSOR_FILTER in k}
                    gpu_temps = {k: int(v) for k, v in sensors.items() if env.GPU_SENSOR_FILTER in k}

                    self._cpu_temp = max(cpu_temps.values() or [0])
                    self._gpu_temp = max(gpu_temps.values() or [0])
                    self._notify_temps()

                    # Read current dimmer value
                    current_dimmer = await self.device.read_dimmer_value()

                    # Calculate new speed based on mode
                    if self._mode == Mode.AUTO:
                        cpu_dimmer = calculate_dimmer_value(self._cpu_temp, env.TEMP_RANGES)
                        gpu_dimmer = calculate_dimmer_value(self._gpu_temp, env.TEMP_RANGES)
                        new_value = max(cpu_dimmer, gpu_dimmer)

                        # Apply step limits
                        if current_dimmer is not None and env.MAX_STEP:
                            if new_value < current_dimmer - env.MAX_STEP:
                                new_value = current_dimmer - env.MAX_STEP

                        # Apply minimum change threshold
                        if current_dimmer is not None and abs(current_dimmer - new_value) < env.IGNORE_LESS_THAN:
                            new_value = current_dimmer
                    else:
                        # Manual mode
                        new_value = self._manual_speed

                    # Update speed if changed
                    if current_dimmer != new_value:
                        logging.info(
                            f"CPU: {self._cpu_temp}, GPU: {self._gpu_temp}. "
                            f"{env.PWM_COMMAND}: {current_dimmer} -> {new_value}"
                        )
                        await self._set_fan_speed(new_value)
                    elif current_dimmer is not None:
                        self._current_speed = current_dimmer
                        self._notify_speed()
                else:
                    self._connected = False
                    self._notify_status()

                await asyncio.sleep(env.DELAY)

        except asyncio.CancelledError:
            logging.debug("Control loop cancelled")
            raise
        except Exception as e:
            logging.exception(f"Error in control loop: {e}")
            self._connected = False
            self._notify_status()

    async def shutdown(self):
        """Shutdown the controller gracefully."""
        await self.stop()
        if self.device.connected:
            await self.device.disconnect()
        self._connected = False
        self._notify_status()
