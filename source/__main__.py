# -*- coding: utf-8 -*-
import asyncio
import time
from typing import List

from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from source.util.env import *  # import dotenv first
from source.util.sensors import get_sensors
from source.util.tools import calculate_dimmer_value
from source.entities.dimmer import Dimmer


async def main():
    device = Dimmer()
    while True:
        if not device.connected:
            await device.connect()

        if not device.connected:
            coms: List[ListPortInfo] = comports()
            coms_match = [_ for _ in coms if DEVICE_NAME in _.description]
            if coms_match:
                com = coms_match[0]
                device.port = com.device
                logging.info(f"Serial Device found at {device.port}")
                await device.connect()

        if device.connected:
            dimmer = await device.read_dimmer_value()
            sensors = get_sensors()

            cpu_temperatures = {k: int(v) for k, v in sensors.items() if 'CPU' in k}
            gpu_temperatures = {k: int(v) for k, v in sensors.items() if 'GPU' in k}

            cpu_temp = max(cpu_temperatures.values() or [0])
            gpu_temp = max(gpu_temperatures.values() or [0])

            cpu_dimmer = calculate_dimmer_value(cpu_temp)
            gpu_dimmer = calculate_dimmer_value(gpu_temp)

            new_value = max(cpu_dimmer, gpu_dimmer)

            if dimmer != new_value:
                logging.info(f"CPU: {cpu_temp}, GPU: {gpu_temp}. {PWM_COMMAND}: {dimmer} -> {new_value}")
                await device.set_dimmer_value(new_value)
        else:
            logging.info(f"No Serial Device found. Sleeping {DELAY}")

        await asyncio.sleep(DELAY)


if __name__ == "__main__":
    asyncio.run(main())
