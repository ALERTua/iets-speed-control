import asyncio
import json
import logging
import re

import aioserial
from serial.serialutil import SerialException

from ..util.config import CONFIG

logger = logging.getLogger(__name__)


def require_connection(func):
    async def wrapper(self, *args, **kwargs):
        if not self.connected:
            await self.connect()
        if not self.connected:
            logger.error("Connection not established.")
            return None

        return await func(self, *args, **kwargs)

    return wrapper


class SerialDevice:
    def __init__(self, port=None, baudrate=None, timeout=None):
        # None means "whatever the configuration says now". Defaulting to CONFIG.device.* in the
        # signature would freeze the values at import time, so a device rebuilt after the user
        # edited the port in the settings panel would still open the old one.
        self.port = CONFIG.device.port if port is None else port
        self.baudrate = CONFIG.device.baudrate if baudrate is None else baudrate
        self.timeout = CONFIG.device.timeout if timeout is None else timeout
        self.serial: aioserial.AioSerial | None = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    @property
    def connected(self) -> bool:
        serial = self.serial
        if serial is None or not serial.is_open or serial.closed:
            return False

        # An unplugged adapter still reports itself open; touching it is what reveals the loss. Any
        # error counts, not only "Access is denied": a port that cannot be read cannot drive the fan.
        try:
            _ = serial.in_waiting
        except SerialException as e:
            logger.debug(f"{self.port} is open but not usable: {e}")
            return False

        return True

    async def connect(self):
        if not self.connected:
            try:
                self.serial = aioserial.AioSerial(
                    port=self.port,
                    baudrate=self.baudrate,
                    write_timeout=self.timeout,
                    timeout=self.timeout,
                )
                logger.info(f"Connected to {self.port}")
            except (OSError, ValueError) as e:
                # SerialException subclasses OSError; ValueError covers bad port/baudrate settings.
                logger.error(f"Error: Unable to connect to {self.port}. {e}")
                return False
        return True

    async def disconnect(self):
        if self.connected and self.serial:
            self.serial.close()
            logger.info(f"Disconnected from {self.port}")

    @require_connection
    async def send_command(self, command):
        if self.serial:
            try:
                await self.serial.write_async((command + "\n").encode())
                await asyncio.sleep(0.1)  # Wait for the command to be processed
            except OSError as e:
                logger.error(f"Error sending command: {e}")

    @require_connection
    async def _read_line(self):
        if self.serial:
            try:
                return (await self.serial.read_until_async()).decode()
            # .strip()
            except OSError as e:
                logger.error(f"Error reading line: {e}")
        return ""

    async def _read_results(self):
        lines = []
        while True:
            line = await self._read_line()
            if not line:
                break

            lines.append(line)
        results = [_.strip() for _ in lines if "RESULT" in _]
        results = [re.sub(".*RESULT = ", "", _) for _ in results]
        output = []
        for result in results:
            try:
                output.append(json.loads(result))
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Error parsing result {result!r}: {e}")
                continue

        return output

    def discard_input(self):
        """Throw away anything already waiting to be read.

        The device answers a set as well as a query, and those replies are never read at the time, so
        they queue up in the driver's buffer. Whatever is in there predates the query about to be
        sent, and returning it made this app read back values it had written itself seconds earlier --
        which the controller then reported as someone changing the dimmer from outside.
        """
        if not self.serial:
            return

        try:
            self.serial.reset_input_buffer()
        except OSError as e:
            logger.debug(f"Could not clear the input buffer: {e}")

    async def read_field_value(self, field_name) -> int | None:
        """Query one field and return its value, or None when the device does not answer."""
        self.discard_input()
        await self.send_command(field_name)

        # Last match rather than first: a device that echoes the command before answering it puts the
        # answer at the end.
        for result in reversed(await self._read_results()):
            if field_name in result:
                return result[field_name]

        return None

    async def set_field_value(self, field_name, value):
        """Set one field. The reply is left in the buffer and dropped by the next read.

        Reading it here would cost a read timeout on every single tick, and nothing needs it.
        """
        await self.send_command(f"{field_name} {value}")
