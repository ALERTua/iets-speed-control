# -*- coding: utf-8 -*-
import logging
import time
from typing import List
from wmi import WMI
from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from source.util.env import *  # import dotenv first
from source.util.logger import logger_setup  # import dotenv first
from source.entities.serial_device import SerialDevice


def calculate_dimmer_value(temperature):
    # Calculate dimmer value based on temperature
    if temperature < MIN_TEMP:
        return 0
    elif temperature > MAX_TEMP:
        return 100
    else:
        return int(((temperature - MIN_TEMP) / (MAX_TEMP - MIN_TEMP)) * 100)


def get_sensors():
    wmi_obj = WMI(namespace="root\\WMI")
    output = {}
    try:
        sensor_values = wmi_obj.AIDA64_SensorValues()
    except:
        logging.error("Error connecting to AIDA64")
        return output

    for v in sensor_values:
        output[v.wmi_property('Label').Value] = v.wmi_property('Value').Value
    return output


def main():
    logger_setup()
    device = SerialDevice()
    while True:
        if not device.connected:
            device.connect()

        if not device.connected:
            coms: List[ListPortInfo] = comports()
            coms_match = [_ for _ in coms if DEVICE_NAME in _.description]
            if coms_match:
                com = coms_match[0]
                device.port = com.device
                logging.info(f"Serial Device found at {device.port}")
                device.connect()

        if device.connected:
            dimmer = device.read_dimmer_value()
            sensors = get_sensors()

            cpu_temperatures = {k: int(v) for k, v in sensors.items() if 'CPU' in k}
            gpu_temperatures = {k: int(v) for k, v in sensors.items() if 'GPU' in k}

            cpu_temp = max(cpu_temperatures.values() or [0])
            gpu_temp = max(gpu_temperatures.values() or [0])

            cpu_dimmer = calculate_dimmer_value(cpu_temp)
            gpu_dimmer = calculate_dimmer_value(gpu_temp)

            new_value = max(cpu_dimmer, gpu_dimmer) or PWM_DEFAULT

            if dimmer != new_value:
                logging.info(f"CPU: {cpu_temp}, GPU: {gpu_temp}. {PWM_COMMAND}: {dimmer} -> {new_value}")
                device.set_dimmer_value(new_value)
        else:
            logging.info(f"No Serial Device found. Sleeping {DELAY}")

        time.sleep(DELAY)


if __name__ == "__main__":
    main()
