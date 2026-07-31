import asyncio
import json
import logging
import re

import aioserial
from serial.serialutil import SerialException

from ..util import env

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
    def __init__(
        self,
        port=env.DEFAULT_PORT,
        baudrate=env.SERIAL_BAUDRATE,
        timeout=env.SERIAL_TIMEOUT,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: aioserial.AioSerial | None = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    @property
    def connected(self):
        output = self.serial and self.serial.is_open and not self.serial.closed
        if output:
            try:
                _ = self.serial.in_waiting  # type: ignore[union-attr]
            except SerialException as e:
                if "Access is denied" in str(e):
                    return False

        return output

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

    async def read_command_result(self):
        results = await self._read_results()
        if not results:
            return

        return results[0]

    async def read_field_value(self, field_name) -> int | None:
        await self.send_command(field_name)
        result = await self.read_command_result()
        return result.get(field_name, None) if result else None

    async def set_field_value(self, field_name, value):
        await self.send_command(f"{field_name} {value}")
        # await asyncio.sleep(0.1)


async def main():
    pass


if __name__ == "__main__":
    asyncio.run(main())
