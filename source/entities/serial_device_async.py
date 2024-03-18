import asyncio
import json
import re
from typing import Optional

import serial_asyncio
import serial

from source.util.env import *

logging.basicConfig(level=logging.INFO)


def require_connection(func):
    async def wrapper(self, *args, **kwargs):
        if not self.connected:
            await self.connect()
        if not self.connected:
            logging.error(f"Connection not established.")
            return None

        return await func(self, *args, **kwargs)

    return wrapper


class SerialDevice:
    def __init__(self, port=DEFAULT_PORT, baudrate=SERIAL_BAUDRATE, dimmer_command=PWM_COMMAND, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dimmer_command = dimmer_command
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    @property
    def connected(self):
        return self.writer and not self.writer.is_closing()

    def reset(self):
        self.reader = self.writer = None

    async def connect(self):
        if not self.connected:
            try:
                self.reader, self.writer = await serial_asyncio.open_serial_connection(
                    url=self.port, baudrate=self.baudrate)
                logging.info(f"Connected to {self.port}")
            except serial.SerialException as e:
                logging.error(f"Error: Unable to connect to {self.port}. {e}")
                self.reset()
                return False
        return True

    async def disconnect(self):
        if self.connected:
            self.writer.close()
            logging.info(f"Disconnected from {self.port}")

        self.reset()

    @require_connection
    async def send_command(self, command):
        try:
            self.writer.write((command + '\n').encode())
            await self.writer.drain()
            await asyncio.sleep(0.1)  # Wait for the command to be processed
        except serial.SerialException as e:
            logging.error(f"Error sending command: {e}")
            self.reset()

    @require_connection
    async def _read_line(self):
        try:
            return (await self.reader.readline()).decode().strip()
        except serial.SerialException as e:
            logging.error(f"Error reading line: {e}")
            self.reset()
            return ''

    async def _read_results(self):
        lines = []
        while not self.reader.at_eof():
            line = await self._read_line()
            if not line:
                break

            lines.append(line)
        results = [_ for _ in lines if 'RESULT' in _]
        results = [re.sub('.*RESULT = ', '', _) for _ in results]
        output = []
        for result in results:
            try:
                output.append(json.loads(result))
            except:
                continue
        return output

    async def read_command_result(self):
        results = await self._read_results()
        if not results:
            return

        output = results[0]
        return output

    async def read_field_value(self, field_name) -> Optional[int]:
        await self.send_command(field_name)
        result = await self.read_command_result()
        output = (result or {}).get(field_name, None)
        return output

    async def set_field_value(self, field_name, value):
        await self.send_command(f"{field_name} {value}")

    async def read_dimmer_value(self) -> Optional[int]:
        return await self.read_field_value(self.dimmer_command)

    async def set_dimmer_value(self, value):
        return await self.set_field_value(self.dimmer_command, value)


async def main():
    sd = SerialDevice()
    await sd.connect()
    await sd.send_command(PWM_COMMAND)
    value = await sd._read_results()

    value_set = await sd.set_dimmer_value(60)
    value_set2 = await sd.set_dimmer_value(0)
    pass


if __name__ == '__main__':
    asyncio.run(main())
