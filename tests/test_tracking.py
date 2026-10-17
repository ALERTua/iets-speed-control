"""Which readings drive the fan: the plausibility of a reading, and the hottest of the tracked sensors."""

import pytest

from iets_speed_control.sensors.base import is_plausible, plausible
from iets_speed_control.util.tracking import Selection, SensorReading, select, tracked_count

# --- plausibility ---------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0.1, 1, 60.0, 150, 150.0])
def test_a_temperature_a_chip_can_have_is_plausible(value):
    assert is_plausible("CPU Package", value)


@pytest.mark.parametrize("value", [0, 0.0, -1, -0.1, 150.1, 151, 1000.0])
def test_a_value_outside_the_bounds_is_not_plausible(value):
    """0 is how several sources report a sensor they lack, and past 150 is a broken reading."""
    assert not is_plausible("CPU Package", value)


@pytest.mark.parametrize("value", [True, False, "60", "60.0", None, [60.0], float("nan"), float("inf")])
def test_a_value_that_is_not_a_real_number_is_not_plausible(value):
    """A bool is an int in Python, so True would otherwise read as 1 °C."""
    assert not is_plausible("CPU Package", value)


@pytest.mark.parametrize(
    "label",
    [
        "CPU Package Limit",
        "Core Distance to TjMax",
        "Sensor Resolution",
        "GPU Warning Temperature",
        "CPU Critical Temperature",
        "TEMPERATURE LIMITS",
    ],
)
def test_a_limit_or_a_margin_is_not_plausible_whatever_its_value(label):
    assert not is_plausible(label, 60.0)


def test_plausible_keeps_what_passes_in_its_order():
    readings = {"B": 70.0, "Zero": 0.0, "A": 40.0, "Hot": 151.0, "Core Limit": 90.0}

    result = plausible(readings)

    assert list(result.items()) == [("B", 70.0), ("A", 40.0)]


# --- tracked_count ---------------------------------------------------------------------------------


def test_the_count_adds_up_the_sensors_of_every_source():
    assert tracked_count({"aida64": ["CPU", "GPU"], "nvidia": ["GPU Core"]}) == 3


def test_a_source_with_nothing_picked_counts_for_none():
    assert tracked_count({"aida64": []}) == 0
    assert tracked_count({}) == 0


# --- select ----------------------------------------------------------------------------------------


def test_the_hottest_of_two_sources_is_chosen():
    selection = select(
        {"aida64": {"CPU Package": 60.0, "SSD": 99.0}, "nvidia": {"GPU Core": 80.0}},
        {"aida64": ["CPU Package"], "nvidia": ["GPU Core"]},
    )

    assert selection.hottest == SensorReading("nvidia", "GPU Core", 80.0)
    assert selection.max_value == 80.0


def test_a_sensor_that_is_not_tracked_never_holds_the_maximum():
    selection = select({"aida64": {"CPU Package": 60.0, "SSD": 99.0}}, {"aida64": ["CPU Package"]})

    assert selection.max_value == 60.0
    assert [reading.label for reading in selection.readings] == ["CPU Package"]


def test_every_tracked_sensor_is_listed_in_the_order_it_is_tracked():
    selection = select(
        {"aida64": {"A": 1.0, "B": 2.0}, "nvidia": {"C": 3.0}},
        {"nvidia": ["C"], "aida64": ["B", "A"]},
    )

    assert [(r.source, r.label, r.value) for r in selection.readings] == [
        ("nvidia", "C", 3.0),
        ("aida64", "B", 2.0),
        ("aida64", "A", 1.0),
    ]


def test_the_same_label_in_two_sources_is_told_apart():
    """Two sources can name a sensor alike, which is why the set is kept per source."""
    selection = select(
        {"aida64": {"GPU": 50.0}, "lenovo-wmi": {"GPU": 72.0}},
        {"aida64": ["GPU"], "lenovo-wmi": ["GPU"]},
    )

    assert selection.hottest == SensorReading("lenovo-wmi", "GPU", 72.0)


@pytest.mark.parametrize("order", [["aida64", "nvidia"], ["nvidia", "aida64"]])
def test_on_a_tie_the_sensor_listed_first_holds_the_maximum(order):
    """Otherwise the sensor marked as the maximum would flicker between two that read the same."""
    readings = {"aida64": {"CPU": 70.0}, "nvidia": {"GPU Core": 70.0}}
    labels = {"aida64": ["CPU"], "nvidia": ["GPU Core"]}

    selection = select(readings, {source: labels[source] for source in order})

    assert selection.hottest is selection.readings[0]
    assert selection.hottest.source == order[0]


def test_on_a_tie_inside_one_source_the_label_listed_first_holds_the_maximum():
    selection = select({"aida64": {"B": 70.0, "A": 70.0}}, {"aida64": ["A", "B"]})

    assert selection.hottest.label == "A"


def test_a_sensor_the_source_did_not_report_has_no_value_and_does_not_count():
    selection = select({"aida64": {"CPU": 55.0}}, {"aida64": ["Gone", "CPU"]})

    assert selection.readings[0] == SensorReading("aida64", "Gone", None)
    assert selection.max_value == 55.0


def test_an_empty_source_has_no_values_and_the_others_still_count():
    selection = select({"aida64": {}, "nvidia": {"GPU Core": 61.0}}, {"aida64": ["CPU"], "nvidia": ["GPU Core"]})

    assert selection.readings[0].value is None
    assert selection.hottest.source == "nvidia"


def test_a_source_that_was_not_read_at_all_has_no_values():
    selection = select({}, {"aida64": ["CPU", "GPU"]})

    assert [reading.value for reading in selection.readings] == [None, None]
    assert selection.hottest is None
    assert selection.max_value is None


def test_a_reading_of_zero_is_a_reading():
    """Plausibility is the sources' job; a value that got here is not discarded for being small."""
    selection = select({"aida64": {"CPU": 0.0}}, {"aida64": ["CPU"]})

    assert selection.max_value == 0.0


def test_a_negative_value_is_still_the_maximum_when_it_is_the_only_one():
    selection = select({"aida64": {"CPU": -3.0}}, {"aida64": ["CPU"]})

    assert selection.max_value == -3.0


def test_an_empty_set_selects_nothing():
    assert select({"aida64": {"CPU": 60.0}}, {}) == Selection((), None)
    assert Selection().max_value is None


def test_a_source_with_nothing_picked_selects_nothing():
    selection = select({"aida64": {"CPU": 60.0}}, {"aida64": []})

    assert selection.readings == ()
    assert selection.max_value is None
