"""Filters: which readings drive the fan, and which of them holds the maximum."""

import pytest

from iets_speed_control.util.filters import DEFAULT_FILTERS, compile_filter, select

READINGS = {
    "13th Gen Intel Core i9-13900HX/Core Max": 87.0,
    "13th Gen Intel Core i9-13900HX/CPU Package": 85.0,
    "NVIDIA GeForce RTX 4090 Laptop GPU/GPU Hot Spot": 76.5,
    "NVIDIA GeForce RTX 4090 Laptop GPU/GPU Core": 70.0,
    "Samsung SSD 990 PRO 1TB/Composite Temperature": 51.0,
}


def test_each_filter_reports_its_hottest_match():
    selection = select(READINGS, ["CPU", "GPU"])

    # "Core Max" has no "CPU" in its name, so the CPU filter catches the package.
    assert [(m.pattern, m.value) for m in selection.matches] == [("CPU", 85.0), ("GPU", 76.5)]
    assert selection.matches[0].label == "13th Gen Intel Core i9-13900HX/CPU Package"


def test_the_hottest_filter_holds_the_maximum():
    selection = select(READINGS, ["GPU", "CPU"])

    assert selection.max_value == 85.0
    assert selection.hottest is selection.matches[1]


def test_matching_ignores_case():
    selection = select({"GPU1 Hotspot": 62.0, "GPU Hot Spot": 76.5}, ["hot ?spot"])

    assert selection.max_value == 76.5


def test_a_filter_is_a_regular_expression():
    selection = select(READINGS, [r"SSD.*Composite"])

    assert selection.max_value == 51.0


def test_a_filter_matching_nothing_says_so_and_does_not_count():
    selection = select(READINGS, ["Nonexistent", "SSD"])

    assert selection.matches[0].value is None
    assert selection.max_value == 51.0


def test_nothing_matched_means_no_maximum():
    selection = select(READINGS, ["Nonexistent"])

    assert selection.hottest is None
    assert selection.max_value is None


def test_on_a_tie_the_filter_listed_first_holds_the_maximum():
    """Otherwise the marked filter could flicker between two that catch the same sensor."""
    selection = select(READINGS, ["Core Max", "i9"])

    assert selection.hottest is selection.matches[0]


def test_the_defaults_are_the_two_filters_the_app_shipped_with():
    assert DEFAULT_FILTERS == ("CPU", "GPU")


@pytest.mark.parametrize("pattern", ["", "   ", None, 42])
def test_an_empty_or_non_text_filter_is_refused(pattern):
    with pytest.raises(ValueError, match="non-empty text"):
        compile_filter(pattern)


def test_a_broken_expression_is_refused_with_the_reason():
    with pytest.raises(ValueError, match="not a valid regular expression"):
        compile_filter("CPU (")


def test_a_filter_is_compiled_once_however_many_ticks_use_it():
    compile_filter.cache_clear()

    for _ in range(5):
        select(READINGS, ["CPU", "GPU"])

    assert compile_filter.cache_info().misses == 2
