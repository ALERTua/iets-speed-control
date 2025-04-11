import os
from source.util.logger import logger_setup
from source.util.tools import strtobool
import logging  # has to be here

from dotenv import load_dotenv

load_dotenv()

VERBOSE = strtobool(os.getenv("VERBOSE", "False"))
logger_setup()
logging.debug(f"env verbose: {VERBOSE}")

DEVICE_NAME = os.getenv("DEVICE_NAME", "USB-Enhanced-SERIAL CH9102")
DEVICE_SERIAL = os.getenv("DEVICE_SERIAL", "568B022419")
DEFAULT_PORT = os.getenv("DEFAULT_PORT", "COM7")
PWM_COMMAND = os.getenv("PWM_COMMAND", "Dimmer")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
SERIAL_TIMEOUT = float(os.getenv("SERIAL_TIMEOUT", "0.3"))
DELAY = float(os.getenv("DELAY", "1.1"))
IGNORE_LESS_THAN = int(os.getenv("IGNORE_LESS_THAN", "3"))
CPU_SENSOR_FILTER = os.getenv("CPU_SENSOR_FILTER", "CPU")
GPU_SENSOR_FILTER = os.getenv("GPU_SENSOR_FILTER", "GPU")
MAX_STEP = int(os.getenv("MAX_STEP", 100))
TEMP_RANGES = os.getenv(
    "TEMP_RANGES",
    "(0, min_temp, dimmer_zero, dimmer_zero),"
    " (min_temp, 70, dimmer_minimum, 50),"
    " (70, 85, 50, 65),"
    " (85, 90, 65, 75),"
    " (90, max_temp, 75, dimmer_maximum)",
)
