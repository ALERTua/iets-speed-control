from source.util.env import *  # import dotenv first
from source.util.logger import logger_setup  # import dotenv first

logger_setup()


def calculate_dimmer_value(temperature, dimmer_minimum=DIMMER_MINIMUM, dimmer_maximum=DIMMER_MAXIMUM,
                           dimmer_zero=DIMMER_ZERO, min_temp=MIN_TEMP, max_temp=MAX_TEMP):
    # Calculate dimmer value based on temperature
    if temperature < min_temp:
        output = dimmer_zero
    elif temperature > max_temp:
        output = dimmer_maximum
    else:
        output = dimmer_minimum + int(((temperature - min_temp) / (max_temp - min_temp)) * dimmer_maximum)
    return output
