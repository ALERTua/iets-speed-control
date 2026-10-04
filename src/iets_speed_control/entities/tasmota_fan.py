"""A fan behind a Tasmota device on a serial port: the speed is one PWM field, Dimmer by default."""

import logging

from serial.tools.list_ports_windows import comports

from ..util.config import CONFIG
from .serial_device import SerialDevice

logger = logging.getLogger(__name__)


class TasmotaSerialFan(SerialDevice):
    """Sets the fan speed as `<command> <value>` on the Tasmota console and reads it back the same way."""

    def __init__(self, port=None, baudrate=None, timeout=None, dimmer_command=None):
        super().__init__(port=port, baudrate=baudrate, timeout=timeout)
        self.dimmer_command = CONFIG.device.pwm_command if dimmer_command is None else dimmer_command

    def describe(self) -> str:
        return f"{self.port} at {self.baudrate} baud"

    async def connect(self) -> bool:
        """Open the configured port; when that fails, look for the device by its description or serial.

        A USB serial adapter can come back on another COM number after a replug, so the port in the
        configuration is only the first guess.
        """
        if await super().connect():
            return True

        found = self._find_port()
        if found is None or found == self.port:
            return False

        self.port = found
        logger.info(f"Serial device found at {self.port}")
        return await super().connect()

    def _find_port(self) -> str | None:
        ports = comports()
        matches = []
        if CONFIG.device.name:
            matches = [port for port in ports if CONFIG.device.name in port.description]
        if CONFIG.device.serial:
            by_serial = [port for port in ports if port.serial_number and CONFIG.device.serial in port.serial_number]
            matches = by_serial or matches

        return matches[0].device if matches else None

    async def read_speed(self) -> int | None:
        return await self.read_field_value(self.dimmer_command)

    async def set_speed(self, value: int) -> None:
        await self.set_field_value(self.dimmer_command, value)


def create_fan() -> TasmotaSerialFan:
    """The fan device the configuration describes, built from the configuration as it is now."""
    return TasmotaSerialFan()
