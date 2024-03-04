from source.util.env import *  # import dotenv first
from source.util.logger import logger_setup  # import dotenv first

logger_setup()


def calculate_dimmer_value(temperature):
    # Calculate dimmer value based on temperature
    if temperature < MIN_TEMP:
        return 0
    elif temperature > MAX_TEMP:
        return 100
    else:
        return int(((temperature - MIN_TEMP) / (MAX_TEMP - MIN_TEMP)) * 100)
