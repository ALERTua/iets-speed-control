from ..util.config import CONFIG
from .serial_device import SerialDevice


class Dimmer(SerialDevice):
    def __init__(self, port=None, baudrate=None, timeout=None, dimmer_command=None):
        super().__init__(port=port, baudrate=baudrate, timeout=timeout)
        self.dimmer_command = CONFIG.device.pwm_command if dimmer_command is None else dimmer_command

    async def read_dimmer_value(self) -> int | None:
        return await self.read_field_value(self.dimmer_command)

    async def set_dimmer_value(self, value):
        return await self.set_field_value(self.dimmer_command, value)


# async def _main():
#     sd = Dimmer()
#     await sd.connect()
#     await sd.send_command(CONFIG.device.pwm_command)
#     value = await sd._read_results()
#
#     value_set = await sd.set_dimmer_value(60)
#     value1 = await sd._read_results()
#     value_set2 = await sd.set_dimmer_value(0)
#     value2 = await sd._read_results()
#     pass
#
#
# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(_main())
