import os

from dotenv import load_dotenv

load_dotenv()

VERBOSE = os.getenv('VERBOSE', False)

DEVICE_NAME = os.getenv('TASMOTA_DEVICE_NAME', 'USB-Enhanced-SERIAL CH9102')
DEFAULT_PORT = os.getenv('DEFAULT_PORT', 'COM7')
DIMMER_COMMAND = os.getenv('DIMMER_COMMAND', 'Dimmer')
DEFAULT_BAUDRATE = int(os.getenv('DEFAULT_BAUDRATE', '115200'))  # Minimum temperature for dimmer level 0
DELAY = float(os.getenv('DELAY', '1.1'))
MIN_TEMP = int(os.getenv('MIN_TEMP', '55'))  # Minimum temperature for dimmer level 0
MAX_TEMP = int(os.getenv('MAX_TEMP', '100'))  # Maximum temperature for dimmer level 100
DIMMER_DEFAULT_SPEED = int(os.getenv('DIMMER_DEFAULT_SPEED', '20'))  # Maximum temperature for dimmer level 100
