import asyncio
from typing import Optional

from source.util.env import *
from source.entities.serial_device import SerialDevice


logging.basicConfig(level=logging.INFO)


class Dimmer(SerialDevice):
    def __init__(self, port=DEFAULT_PORT, baudrate=SERIAL_BAUDRATE, timeout=0.3, dimmer_command=PWM_COMMAND):
        super().__init__(port=port, baudrate=baudrate, timeout=timeout)
        self.dimmer_command = dimmer_command

    async def read_dimmer_value(self) -> Optional[int]:
        return await self.read_field_value(self.dimmer_command)

    async def set_dimmer_value(self, value):
        return await self.set_field_value(self.dimmer_command, value)


async def main():
    sd = Dimmer()
    await sd.connect()
    await sd.send_command(PWM_COMMAND)
    value = await sd._read_results()

    value_set = await sd.set_dimmer_value(60)
    value1 = await sd._read_results()
    value_set2 = await sd.set_dimmer_value(0)
    value2 = await sd._read_results()
    pass


if __name__ == '__main__':
    asyncio.run(main())
