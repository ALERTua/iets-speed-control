import os

from dotenv import load_dotenv

from .tools import strtobool

load_dotenv()

# Logging is configured by the entrypoints, not here -- importing settings must not touch global state.
VERBOSE = strtobool(os.getenv("VERBOSE", "False"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "")
LOG_FILE = os.getenv("LOG_FILE", "")

DEVICE_NAME = os.getenv("DEVICE_NAME", "USB-Enhanced-SERIAL CH9102")
DEVICE_SERIAL = os.getenv("DEVICE_SERIAL", "568B022419")
DEFAULT_PORT = os.getenv("DEFAULT_PORT", "COM7")
PWM_COMMAND = os.getenv("PWM_COMMAND", "Dimmer")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
SERIAL_TIMEOUT = float(os.getenv("SERIAL_TIMEOUT", "0.3"))
DELAY = float(os.getenv("DELAY", "1.1"))
IGNORE_LESS_THAN = int(os.getenv("IGNORE_LESS_THAN", "0"))
TEMP_WINDOW = int(os.getenv("TEMP_WINDOW", "5"))
RESYNC_EVERY = int(os.getenv("RESYNC_EVERY", "30"))
SENSOR_PROVIDER = os.getenv("SENSOR_PROVIDER", "aida64")
LHM_WEB_URL = os.getenv("LHM_WEB_URL", "http://localhost:8085/data.json")
LHM_WEB_TIMEOUT = float(os.getenv("LHM_WEB_TIMEOUT", "1.0"))
LHM_WEB_USERNAME = os.getenv("LHM_WEB_USERNAME", "")
LHM_WEB_PASSWORD = os.getenv("LHM_WEB_PASSWORD", "")
CPU_SENSOR_FILTER = os.getenv("CPU_SENSOR_FILTER", "CPU")
GPU_SENSOR_FILTER = os.getenv("GPU_SENSOR_FILTER", "GPU")
MAX_STEP = int(os.getenv("MAX_STEP", "100"))
TEMP_RANGES = os.getenv(
    "TEMP_RANGES",
    "(0, min_temp, dimmer_zero, dimmer_zero),"
    " (min_temp, 70, dimmer_minimum, 50),"
    " (70, 85, 50, 65),"
    " (85, 90, 65, 75),"
    " (90, max_temp, 75, dimmer_maximum)",
)
