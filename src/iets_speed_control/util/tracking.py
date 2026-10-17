"""Which readings drive the fan: the sensors the user tracks, from any number of sources, and the hottest of them.

A tracked sensor is one exact label of one source. Sources name their sensors their own way and two of
them can use the same label, so the set is kept per source: {source code name: [labels]}.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

Tracked = Mapping[str, Sequence[str]]


class SourceState(Enum):
    """How a source answered its last read."""

    OK = "ok"
    # Answered nothing: its app is closed, unreachable, or broke.
    SILENT = "silent"
    # Cannot answer in a process without administrator rights.
    NEEDS_ADMIN = "needs admin"
    # Its hardware is powered off, as an idle laptop's NVIDIA GPU is: cold, not broken.
    ASLEEP = "asleep"


@dataclass(frozen=True)
class SensorReading:
    """One tracked sensor and its latest value, or None when the source gave no value for it."""

    source: str
    label: str
    value: float | None = None


@dataclass(frozen=True)
class Selection:
    """Every tracked sensor's latest value, and which of them holds the maximum that drives the curve."""

    readings: tuple[SensorReading, ...] = ()
    hottest: SensorReading | None = None

    @property
    def max_value(self) -> float | None:
        return self.hottest.value if self.hottest else None


def tracked_count(tracked: Tracked) -> int:
    """How many sensors are tracked; a source added with nothing picked yet counts for none."""
    return sum(len(labels) for labels in tracked.values())


def select(readings: Mapping[str, Mapping[str, float]], tracked: Tracked) -> Selection:
    """Look up each tracked sensor in its source's readings and pick the hottest.

    On a tie the sensor listed first wins, so the one marked as the maximum does not flicker between two
    sensors that read the same.
    """
    found = []
    hottest = None
    hottest_value = float("-inf")
    for source, labels in tracked.items():
        values = readings.get(source, {})
        for label in labels:
            reading = SensorReading(source, label, values.get(label))
            found.append(reading)
            if reading.value is not None and reading.value > hottest_value:
                hottest, hottest_value = reading, reading.value

    return Selection(tuple(found), hottest)
