"""Which readings drive the fan: a list of filters, and the hottest reading any of them matches.

A filter is a regular expression searched in a reading's label, ignoring case, so "hot ?spot" finds both
AIDA64's "GPU1 Hotspot" and LibreHardwareMonitor's "GPU Hot Spot". Each source keeps its own list,
because each source names its sensors its own way.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# What a source gets until its own list is chosen: the two filters the app shipped with.
DEFAULT_FILTERS = ("CPU", "GPU")


def compile_filter(pattern: str) -> re.Pattern:
    """Compile one filter, with the reason in the message when it is not a usable expression."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"a filter has to be non-empty text, got {pattern!r}")

    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"{pattern!r} is not a valid regular expression: {e}") from None


@dataclass(frozen=True)
class FilterMatch:
    """What one filter found: its hottest reading, or nothing."""

    pattern: str
    value: float | None = None
    label: str | None = None


@dataclass(frozen=True)
class Selection:
    """Every filter's result, and which of them holds the maximum that drives the curve."""

    matches: tuple[FilterMatch, ...] = ()
    hottest: FilterMatch | None = None

    @property
    def max_value(self) -> float | None:
        return self.hottest.value if self.hottest else None


def select(readings: Mapping[str, float], patterns: Sequence[str]) -> Selection:
    """Apply each filter to the readings and pick the hottest match overall.

    On a tie the filter listed first wins, so the one marked as the maximum does not flicker between
    two filters that catch the same sensor.
    """
    matches = []
    hottest = None
    hottest_value = float("-inf")
    for pattern in patterns:
        regex = compile_filter(pattern)
        found = [(value, label) for label, value in readings.items() if regex.search(label)]
        if not found:
            matches.append(FilterMatch(pattern))
            continue

        value, label = max(found, key=lambda item: item[0])
        match = FilterMatch(pattern, value, label)
        matches.append(match)
        if value > hottest_value:
            hottest, hottest_value = match, value

    return Selection(tuple(matches), hottest)
