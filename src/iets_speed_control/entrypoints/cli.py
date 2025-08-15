import asyncio
import logging
from typing import List

from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from ..util import env
from ..util.sensors import get_sensors
from ..util.tools import calculate_dimmer_value
from ..entities.dimmer import Dimmer


class DimmerException(Exception):
    pass


async def _cli():
    device = Dimmer()
    initial = True
    try:
        while True:
            if not device.connected:
                await device.connect()

            if not device.connected:
                coms: List[ListPortInfo] = comports()
                coms_match = []
                if env.DEVICE_NAME:
                    coms_match = [_ for _ in coms if env.DEVICE_NAME in _.description]
                if env.DEVICE_SERIAL:
                    coms_match = [
                        _
                        for _ in coms
                        if _.serial_number and env.DEVICE_SERIAL in _.serial_number
                    ] or coms_match

                if coms_match:
                    com = coms_match[0]
                    device.port = com.device
                    logging.info(f"Serial Device found at {device.port}")
                    await device.connect()

            sensors = get_sensors()
            cpu_temperatures = {
                k: int(v) for k, v in sensors.items() if env.CPU_SENSOR_FILTER in k
            }
            gpu_temperatures = {
                k: int(v) for k, v in sensors.items() if env.GPU_SENSOR_FILTER == k
            }

            cpu_temp = max(cpu_temperatures.values() or [0])
            gpu_temp = max(gpu_temperatures.values() or [0])

            if device.connected:
                dimmer = await device.read_dimmer_value()

                if initial:
                    logging.info(
                        f"Starting with CPU: {cpu_temp}, GPU: {gpu_temp}. {env.PWM_COMMAND}: {dimmer}"
                    )
                    initial = False

                cpu_dimmer = calculate_dimmer_value(cpu_temp, env.TEMP_RANGES)
                gpu_dimmer = calculate_dimmer_value(gpu_temp, env.TEMP_RANGES)

                new_value = max(cpu_dimmer, gpu_dimmer)
                if dimmer is not None and env.MAX_STEP:
                    # if new_value > dimmer + MAX_STEP:  # limit up step
                    #     new_value = dimmer + MAX_STEP
                    if new_value < dimmer - env.MAX_STEP:  # limit down step
                        new_value = dimmer - env.MAX_STEP

                if (
                    dimmer is not None
                    and abs(dimmer - new_value) < env.IGNORE_LESS_THAN
                ):
                    # logging.info(f"Skipping too low: {dimmer} -> {new_value}")
                    pass
                elif dimmer != new_value:
                    logging.info(
                        f"CPU: {cpu_temp}, GPU: {gpu_temp}. {env.PWM_COMMAND}: {dimmer} -> {new_value}"
                    )
                    await device.set_dimmer_value(new_value)

                await asyncio.sleep(env.DELAY)
            else:
                logging.info(f"No device connected. CPU: {cpu_temp}, GPU: {gpu_temp}")
                raise DimmerException

    except asyncio.CancelledError:
        logging.info("Setting 0")
        await device.set_dimmer_value(0)
        await asyncio.sleep(0.5)
    except DimmerException:
        logging.info("Sleeping 10")
        await asyncio.sleep(10)
        return await _cli()


def cli():
    return asyncio.run(_cli())


if __name__ == "__main__":
    cli()
