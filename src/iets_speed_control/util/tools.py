# do not import env here

import statistics
from collections import deque


class MedianSmoother:
    """Rolling median over the last `window` readings.

    Median rather than a moving average on purpose: a single spurious spike is discarded outright
    instead of being blended into the result, and a sensor sitting between two range boundaries
    stops flip-flopping the fan.  A window of 1 disables smoothing.
    """

    def __init__(self, window: int = 1):
        self.window = max(1, int(window))
        self._samples: deque[float] = deque(maxlen=self.window)

    def add(self, value: float) -> float:
        """Record a reading and return the smoothed value."""
        self._samples.append(float(value))
        return statistics.median(self._samples)

    def reset(self):
        """Drop history, e.g. after the sensor source went away."""
        self._samples.clear()

    @property
    def samples(self) -> tuple[float, ...]:
        return tuple(self._samples)


def calculate_dimmer_value(temperature, temperature_ranges):
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
            output = dimmer_down + int(((temperature - temp_down) / (temp_up - temp_down)) * (dimmer_up - dimmer_down))
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


def strtobool(val):  # distutil strtobool
    """Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return 1
    elif val in ("n", "no", "f", "false", "off", "0"):
        return 0
    else:
        raise ValueError(f"invalid truth value {val!r}")
