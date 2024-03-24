from source.util.env import *  # import dotenv first
from source.util.logger import logger_setup  # import dotenv first

logger_setup()


def calculate_dimmer_value(temperature, temperature_ranges=TEMP_RANGES):
    if isinstance(temperature_ranges, str):
        temperature_ranges = eval(temperature_ranges)
    output = None
    temps_down = set()
    temps_up = set()
    dimmers_down = set()
    dimmers_up = set()

    # Calculate dimmer value based on temperature
    # output = dimmer_minimum + int(((temperature - min_temp) / (max_temp - min_temp)) * dimmer_maximum)
    for temp_down, temp_up, dimmer_down, dimmer_up in temperature_ranges:
        temps_down.add(temp_down)
        temps_up.add(temp_up)
        dimmers_down.add(dimmer_down)
        dimmers_up.add(dimmer_up)
        if temp_down <= temperature < temp_up:
            # Calculate linear dimmer value within the current range
            output = dimmer_down + int(((temperature - temp_down) / (temp_up - temp_down))
                                       * (dimmer_up - dimmer_down))
            break

    if output is None:
        min_temp = min(temps_down)
        max_temp = max(temps_up)
        min_dimmer = min(dimmers_down)
        max_dimmer = max(dimmers_up)
        if temperature >= max_temp:
            output = max_dimmer
        elif temperature <= min_temp:
            output = min_dimmer
        else:
            output = min_dimmer
    return output


if __name__ == '__main__':
    temp_35 = calculate_dimmer_value(35)
    temp_40 = calculate_dimmer_value(40)
    temp_50 = calculate_dimmer_value(50)
    temp_60 = calculate_dimmer_value(60)
    temp_70 = calculate_dimmer_value(70)
    temp_80 = calculate_dimmer_value(80)
    temp_90 = calculate_dimmer_value(90)
    temp_100 = calculate_dimmer_value(100)
    pass
