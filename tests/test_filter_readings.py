"""Home's filter list: each filter's reading, and the one that gives the maximum marked."""

import pytest

from iets_speed_control.util.filters import select

ctk = pytest.importorskip("customtkinter")

READINGS = {"Fake CPU/Core Max": 61.0, "Fake CPU/Package": 58.5, "Fake GPU/Hot Spot": 44.0}


@pytest.fixture
def readings_list(tk_root):
    from iets_speed_control.gui.filter_readings import FilterReadings

    widget = FilterReadings(tk_root)
    widget.pack()
    try:
        yield widget
    finally:
        widget.destroy()


def texts(row):
    return [label.cget("text") for label in row]


def test_each_filter_shows_its_reading_and_sensor(readings_list):
    readings_list.show(select(READINGS, ["CPU", "GPU"]))

    assert texts(readings_list.rows[0])[1:] == ["61 °C", "Fake CPU/Core Max"]
    assert texts(readings_list.rows[1])[1:] == ["44 °C", "Fake GPU/Hot Spot"]


def test_the_filter_giving_the_maximum_is_marked(readings_list):
    from iets_speed_control.gui.theme import MAX_COLOR, MUTED

    readings_list.show(select(READINGS, ["GPU", "CPU"]))

    gpu, cpu = readings_list.rows
    assert cpu[0].cget("text").startswith("▶"), "the marked line is the one the curve follows"
    assert cpu[1].cget("text_color") == MAX_COLOR
    assert not gpu[0].cget("text").startswith("▶")
    assert gpu[1].cget("text_color") == MUTED


def test_a_filter_matching_nothing_is_called_out(readings_list):
    from iets_speed_control.gui.filter_readings import NO_MATCH
    from iets_speed_control.gui.theme import ERROR_COLOR

    readings_list.show(select(READINGS, ["Nonexistent", "CPU"]))

    assert texts(readings_list.rows[0])[2] == NO_MATCH
    assert readings_list.rows[0][2].cget("text_color") == ERROR_COLOR


def test_the_rows_are_reused_while_the_filters_stay_the_same(readings_list):
    """A reading arrives every tick; rebuilding the labels each time would churn Tk for nothing."""
    readings_list.show(select(READINGS, ["CPU", "GPU"]))
    before = [label for row in readings_list.rows for label in row]

    readings_list.show(select({**READINGS, "Fake CPU/Core Max": 70.0}, ["CPU", "GPU"]))

    assert [label for row in readings_list.rows for label in row] == before
    assert readings_list.rows[0][1].cget("text") == "70 °C"


def test_a_changed_filter_list_rebuilds_the_rows(readings_list):
    readings_list.show(select(READINGS, ["CPU", "GPU"]))

    readings_list.show(select(READINGS, ["Hot Spot"]))

    assert len(readings_list.rows) == 1
    assert readings_list.rows[0][0].cget("text").endswith("Hot Spot")


def test_home_shows_the_list_from_the_controllers_report(tk_root):
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()
    try:
        panel._post("temps", (61, select(READINGS, ["CPU", "GPU"])))
        panel._drain()

        assert "61" in panel.value_labels["Max"].cget("text")
        assert len(panel.filter_readings.rows) == 2
    finally:
        panel.destroy()
