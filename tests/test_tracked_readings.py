"""Home's list of the tracked sensors: each one's reading, the hottest marked, and why one has none."""

import asyncio

import pytest
from test_control_smoothing import FakeDevice

from iets_speed_control.util.config import CONFIG
from iets_speed_control.util.tracking import SensorReading, SourceState, select

ctk = pytest.importorskip("customtkinter")


class FakeSource:
    """Any object with a name and get_temperatures() is a source; `asleep` is what the nvidia one sets."""

    def __init__(self, name, readings=None, asleep=False):
        self.name = name
        self.readings = dict(readings or {})
        self.asleep = asleep

    def get_temperatures(self):
        return dict(self.readings)


@pytest.fixture(scope="module")
def home(tk_root):
    """One Home panel for the module: building its graph and labels per test is the slow part."""
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    widget = StatusPanel(tk_root, SpeedController())
    widget.stop_polling()
    widget.pack(fill="both", expand=True)
    tk_root.update()
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


@pytest.fixture
def track(home, monkeypatch):
    """Give Home a controller that tracks `tracked` from `sources`, and run one tick of it."""
    from iets_speed_control.controller import SpeedController

    def tick(tracked, sources):
        monkeypatch.setattr(CONFIG.sensors, "tracked", tracked)
        controller = SpeedController(sources=sources)
        controller.device = FakeDevice(0)
        asyncio.run(controller._tick())
        home.controller = controller
        home._temps = None
        home._monitoring = None
        home._post("temps", (controller.max_temp, controller.selection))
        home._drain()
        return controller

    return tick


def texts(row):
    return [label.cget("text") for label in row]


def test_every_tracked_sensor_of_every_source_is_listed_and_the_hottest_marked(home, track):
    from iets_speed_control.gui.theme import MAX_COLOR, MUTED

    track(
        {"aida64": ["CPU Package"], "nvidia": ["GPU Core"]},
        {
            "aida64": FakeSource("aida64", {"CPU Package": 61.0, "CPU Core #1": 63.0}),
            "nvidia": FakeSource("nvidia", {"GPU Core": 70.0}),
        },
    )

    cpu, gpu = home.tracked_readings.rows
    assert texts(cpu) == ["AIDA64", "CPU Package", "61 °C"], "an untracked sensor of the source must not show"
    assert texts(gpu) == ["▶ NVIDIA driver (NVAPI)", "GPU Core", "70 °C"]
    assert gpu[2].cget("text_color") == MAX_COLOR, "the marked line is the one the curve follows"
    assert cpu[2].cget("text_color") == MUTED
    assert "70" in home.value_labels["Max"].cget("text")


def test_a_tracked_sensor_the_source_does_not_report_says_so(home, track):
    from iets_speed_control.gui.theme import ERROR_COLOR
    from iets_speed_control.gui.tracked_readings import NO_READING

    track({"aida64": ["CPU Package", "Gone"]}, {"aida64": FakeSource("aida64", {"CPU Package": 61.0})})

    gone = home.tracked_readings.rows[1]
    assert texts(gone)[1:] == ["Gone", NO_READING]
    assert gone[2].cget("text_color") == ERROR_COLOR


def test_a_sleeping_gpu_reads_asleep_without_an_alarm(home, track):
    from iets_speed_control.gui.theme import ERROR_COLOR
    from iets_speed_control.gui.tracked_readings import ASLEEP

    track(
        {"aida64": ["CPU Package"], "nvidia": ["GPU Core"]},
        {"aida64": FakeSource("aida64", {"CPU Package": 61.0}), "nvidia": FakeSource("nvidia", asleep=True)},
    )

    gpu = home.tracked_readings.rows[1]
    assert gpu[2].cget("text") == ASLEEP
    assert gpu[2].cget("text_color") != ERROR_COLOR, "a GPU powered off to save energy is not a fault"


def test_without_any_reading_the_max_cell_says_the_fan_runs_at_the_default_speed(home, track):
    track({"aida64": ["CPU Package"]}, {"aida64": FakeSource("aida64", {})})

    assert home.controller.fallback, "test setup: the tick must have had nothing to go on"
    assert home.value_labels["Max"].cget("text") == "--"
    assert home.max_note.winfo_manager()
    assert f"default {CONFIG.control.default_speed} %" in home.max_note.cget("text")


def test_a_reading_puts_the_max_cell_back(home, track):
    track({"aida64": ["CPU Package"]}, {"aida64": FakeSource("aida64", {})})

    track({"aida64": ["CPU Package"]}, {"aida64": FakeSource("aida64", {"CPU Package": 55.0})})

    assert home.value_labels["Max"].cget("text") == "55 °C"
    assert not home.max_note.winfo_manager()


def test_with_nothing_tracked_home_says_monitoring_stopped(home, track):
    from iets_speed_control.gui.tracked_readings import NOT_MONITORING

    track({}, {})

    assert home.tracked_readings.rows == []
    assert home.tracked_readings.message.cget("text") == NOT_MONITORING
    assert home.tracked_readings.message.winfo_manager()
    assert home.value_labels["Max"].cget("text") == "--"
    assert not home.max_note.winfo_manager(), "nothing tracked is a choice, not a missing reading"


def test_unchecking_the_last_sensor_shows_monitoring_stopped_before_any_tick(home, track, monkeypatch):
    """With the device unplugged the loop reports nothing, so Home has to notice on its own."""
    from iets_speed_control.gui.tracked_readings import NOT_MONITORING

    track({"aida64": ["CPU Package"]}, {"aida64": FakeSource("aida64", {"CPU Package": 61.0})})

    monkeypatch.setattr(CONFIG.sensors, "tracked", {"aida64": []})
    home._drain()

    assert home.tracked_readings.message.cget("text") == NOT_MONITORING
    assert home.tracked_readings.message.winfo_manager()


def test_the_rows_are_reused_while_the_tracked_sensors_stay_the_same(home, track):
    """A reading arrives every tick; rebuilding the labels each time would churn Tk for nothing."""
    sources = {"aida64": FakeSource("aida64", {"CPU Package": 61.0, "GPU Hot Spot": 44.0})}
    tracked = {"aida64": ["CPU Package", "GPU Hot Spot"]}
    track(tracked, sources)
    before = [label for row in home.tracked_readings.rows for label in row]

    home.tracked_readings.show(select({"aida64": {"CPU Package": 70.0, "GPU Hot Spot": 44.0}}, tracked))

    assert [label for row in home.tracked_readings.rows for label in row] == before
    assert home.tracked_readings.rows[0][2].cget("text") == "70 °C"


def test_a_shorter_list_drops_the_surplus_rows(home):
    from iets_speed_control.util.tracking import Selection

    readings = tuple(SensorReading("aida64", f"Sensor {index}", 50.0) for index in range(3))
    home.tracked_readings.show(Selection(readings, readings[0]))

    home.tracked_readings.show(Selection(readings[:1], readings[0]))

    assert len(home.tracked_readings.rows) == 1
    assert len(home.tracked_readings.grid_slaves()) == 3, "the surplus labels must be gone, not just forgotten"


def test_the_reason_line_names_each_silent_source(home, track):
    track(
        {"aida64": ["CPU Package"], "lhm-web": ["CPU/Core Max"]},
        {"aida64": FakeSource("aida64", {"CPU Package": 61.0}), "lhm-web": FakeSource("lhm-web", {})},
    )

    home._apply_status(connected=True, running=True, sensors_ok=home.controller.sensors_ok)

    text = home.status_label.cget("text")
    assert "No temperatures from LibreHardwareMonitor (web server)" in text
    assert "AIDA64" not in text, "a source that answered is not part of the problem"


def test_a_source_that_needs_admin_rights_is_told_to_restart(home, track, monkeypatch):
    from iets_speed_control.sensors import base

    monkeypatch.setattr(base, "is_elevated", lambda: False)
    lenovo = FakeSource("lenovo-wmi", {})
    lenovo.requires_admin = True
    track({"lenovo-wmi": ["CPU"]}, {"lenovo-wmi": lenovo})
    assert home.controller.source_states["lenovo-wmi"] is SourceState.NEEDS_ADMIN, "test setup"

    home._apply_status(connected=True, running=True, sensors_ok=home.controller.sensors_ok)

    assert "Lenovo Legion (WMI): restart as administrator" in home.status_label.cget("text")


def test_a_sleeping_gpu_turns_nothing_red(home, track):
    from iets_speed_control.gui.status import DISCONNECTED_COLOR

    track({"nvidia": ["GPU Core"]}, {"nvidia": FakeSource("nvidia", asleep=True)})

    home._apply_status(connected=True, running=True, sensors_ok=home.controller.sensors_ok)

    assert home.controller.sensors_ok
    assert home.status_label.cget("text_color") != DISCONNECTED_COLOR
    assert "No temperatures" not in home.status_label.cget("text")
