import os
from source.util.logger import logger_setup
import logging  # has to be here
try:
    # noinspection PyUnresolvedReferences
    from distutils.util import strtobool
except:
    from pip._internal.utils.misc import strtobool

from dotenv import load_dotenv

load_dotenv()


VERBOSE = strtobool(os.getenv('VERBOSE', 'False'))

logger_setup()
logging.debug(f"env verbose: {VERBOSE}")

DEVICE_NAME = os.getenv('DEVICE_NAME', 'USB-Enhanced-SERIAL CH9102')
DEFAULT_PORT = os.getenv('DEFAULT_PORT', 'COM7')
PWM_COMMAND = os.getenv('PWM_COMMAND', 'Dimmer')
SERIAL_BAUDRATE = int(os.getenv('SERIAL_BAUDRATE', '115200'))  # Minimum temperature for dimmer level 0
DELAY = float(os.getenv('DELAY', '1.1'))
MIN_TEMP = int(os.getenv('MIN_TEMP', '55'))  # Minimum temperature for dimmer level 0
MAX_TEMP = int(os.getenv('MAX_TEMP', '100'))  # Maximum temperature for dimmer level 100
PWM_DEFAULT = int(os.getenv('PWM_DEFAULT', '20'))  # Maximum temperature for dimmer level 100
