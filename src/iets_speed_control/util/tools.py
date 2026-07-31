# do not import env here

import statistics
from collections import deque
from itertools import pairwise


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


CURVE_TEMP_CEILING = 200  # upper bound of the trailing shelf; above it the fallback clamps anyway


def normalize_ranges(temperature_ranges):
    """Accept either the configured string form or an already structured sequence of ranges."""
    if isinstance(temperature_ranges, str):
        # Legacy config format; replaced by structured YAML in plan 06.
        temperature_ranges = eval(temperature_ranges)
    return tuple(tuple(r) for r in temperature_ranges)


def curve_to_ranges(points):
    """Turn curve points [(temp, percent), ...] into the range tuples the controller evaluates.

    Consecutive points become one linear range each, plus flat shelves before the first point and
    after the last, so a temperature outside the curve holds the nearest percentage instead of
    falling back to an unrelated value.
    """
    ordered = sorted((float(t), float(p)) for t, p in points)
    if not ordered:
        raise ValueError("a curve needs at least one point")

    ranges = []
    first_temp, first_pct = ordered[0]
    if first_temp > 0:
        ranges.append((0.0, first_temp, first_pct, first_pct))

    for (temp, pct), (next_temp, next_pct) in pairwise(ordered):
        if next_temp > temp:  # skip degenerate ranges rather than dividing by zero later
            ranges.append((temp, next_temp, pct, next_pct))

    last_temp, last_pct = ordered[-1]
    if last_temp < CURVE_TEMP_CEILING:
        ranges.append((last_temp, float(CURVE_TEMP_CEILING), last_pct, last_pct))

    return tuple(ranges)


def ranges_to_curve(temperature_ranges):
    """Derive curve points from range tuples, for seeding the editor from existing configuration.

    Lossy on purpose: a range whose endpoints disagree with its neighbour's collapses to a single
    point. It is a starting shape for the editor, not a round-trip guarantee.
    """
    ranges = normalize_ranges(temperature_ranges)
    if not ranges:
        return []

    points = {}
    for temp_down, temp_up, dimmer_down, dimmer_up in ranges:
        points[float(temp_down)] = float(dimmer_down)
        points[float(temp_up)] = float(dimmer_up)

    # Drop the artificial shelves: a leading 0 C point and the ceiling carry no user intent.
    points.pop(0.0, None)
    points.pop(float(CURVE_TEMP_CEILING), None)

    return sorted(points.items())


def calculate_dimmer_value(temperature, temperature_ranges):
    temperature_ranges = normalize_ranges(temperature_ranges)
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

    # Always an int: the PWM value goes onto the wire as "<command> <value>", and curve points are
    # floats, so without this the device would receive "Dimmer 49.0".
    return round(output)


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
