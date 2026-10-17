"""The control loop on a tracked set: the hottest tracked reading from any source, and what the fan does when
there is nothing to go on.

Ticks are run one at a time (`_tick`) against a fake device, so each test reads the state after exactly the
reads it scripted. Sources are given by their real code names, because the controller reads the sources of
`sensors.tracked` and creates any name it was not given.
"""

import asyncio
import logging
import threading

import pytest
from test_control_smoothing import FakeDevice

from iets_speed_control.controller import Mode, SpeedController, read_source
from iets_speed_control.sensors import base as sensors_base
from iets_speed_control.util.config import CONFIG
from iets_speed_control.util.tracking import SourceState

DEFAULT_SPEED = 37  # unlike every value the curve below gives for the temperatures used here


class Source:
    """A scripted source: fixed readings, or a fault, or a sleeping GPU."""

    def __init__(self, readings=None, *, raises=None, asleep=False, requires_admin=False):
        self.name = "scripted"
        self.readings = readings or {}
        self.raises = raises
        self.asleep = asleep
        self.requires_admin = requires_admin
        self.calls = 0

    def get_temperatures(self):
        self.calls += 1
        if self.raises:
            raise self.raises
        return dict(self.readings)


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """Every temperature is its own percentage, nothing is smoothed or held back, and the default is odd."""
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "temp_window", 1)
    monkeypatch.setattr(CONFIG.control, "max_step", 100)
    monkeypatch.setattr(CONFIG.control, "ignore_less_than", 0)
    monkeypatch.setattr(CONFIG.control, "curve", [[0, 0], [100, 100]])
    monkeypatch.setattr(CONFIG.control, "default_speed", DEFAULT_SPEED)


def make(tracked: dict, sources: dict, fan: int = 0) -> SpeedController:
    CONFIG.sensors.tracked = {source: list(labels) for source, labels in tracked.items()}
    controller = SpeedController(sources=sources)
    controller.device = FakeDevice(fan)
    return controller


# --- read_source --------------------------------------------------------------------------------


def test_a_source_that_answers_is_ok_and_only_plausible_readings_come_back():
    source = Source({"CPU Package": 61.0, "GPU Core": 0.0, "CPU Limit": 100.0, "Hot": 151.0})

    assert read_source(source) == (SourceState.OK, {"CPU Package": 61.0})


def test_a_source_with_only_unusable_readings_counts_as_silent():
    """0 °C is how a source reports a sensor it lacks, so a source that has nothing else gave no reading."""
    assert read_source(Source({"GPU Core": 0.0})) == (SourceState.SILENT, {})


def test_an_empty_answer_is_silent():
    assert read_source(Source()) == (SourceState.SILENT, {})


def test_a_source_that_raises_is_silent_and_the_error_is_logged(caplog):
    with caplog.at_level(logging.ERROR):
        result = read_source(Source(raises=OSError("WMI is not answering")))

    assert result == (SourceState.SILENT, {})
    assert "WMI is not answering" in caplog.text


def test_a_source_that_needs_admin_rights_says_so_when_it_has_none(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: False)

    assert read_source(Source(requires_admin=True)) == (SourceState.NEEDS_ADMIN, {})


def test_a_source_that_needs_admin_rights_is_only_silent_when_it_has_them(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: True)

    assert read_source(Source(requires_admin=True)) == (SourceState.SILENT, {})


def test_a_sleeping_source_is_asleep_not_silent():
    assert read_source(Source(asleep=True)) == (SourceState.ASLEEP, {})


def test_missing_admin_rights_are_reported_before_sleep(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: False)

    assert read_source(Source(requires_admin=True, asleep=True)) == (SourceState.NEEDS_ADMIN, {})


def test_a_sleeping_source_that_still_answers_is_ok():
    """The flag is only the reason for an empty answer, not a reason to drop one."""
    assert read_source(Source({"GPU Core": 40.0}, asleep=True)) == (SourceState.OK, {"GPU Core": 40.0})


# --- following the hottest tracked sensor -------------------------------------------------------------


async def test_the_fan_follows_the_hottest_tracked_sensor_of_all_sources():
    controller = make(
        {"aida64": ["CPU Package"], "nvidia": ["GPU Core"]},
        {"aida64": Source({"CPU Package": 60.0}), "nvidia": Source({"GPU Core": 80.0})},
    )

    await controller._tick()

    assert controller.max_temp == 80
    assert controller.device.writes == [80]
    assert controller.selection.hottest.source == "nvidia"
    assert not controller.fallback
    assert controller.sensors_ok


async def test_only_the_tracked_sensors_count():
    controller = make({"aida64": ["CPU Package"]}, {"aida64": Source({"CPU Package": 60.0, "SSD": 95.0})})

    await controller._tick()

    assert controller.max_temp == 60


async def test_only_the_tracked_sources_are_read():
    other = Source({"GPU Core": 90.0})
    controller = make({"aida64": ["CPU"]}, {"aida64": Source({"CPU": 50.0}), "nvidia": other})

    await controller._tick()

    assert other.calls == 0
    assert controller.max_temp == 50


async def test_the_readings_of_a_source_are_kept_whether_or_not_they_are_tracked():
    """Settings lists every sensor a source offers, so the untracked ones must stay available."""
    controller = make({"aida64": ["CPU"]}, {"aida64": Source({"CPU": 50.0, "SSD": 40.0, "Dead": 0.0})})

    await controller._tick()

    assert controller.readings == {"aida64": {"CPU": 50.0, "SSD": 40.0}}


async def test_the_sources_are_read_at_the_same_time():
    """A slow source must not add its wait to the others'. Each waits for the other to be inside its read."""
    barrier = threading.Barrier(2, timeout=3)

    class Meets(Source):
        def get_temperatures(self):
            barrier.wait()
            return super().get_temperatures()

    controller = make(
        {"aida64": ["CPU"], "nvidia": ["GPU Core"]},
        {"aida64": Meets({"CPU": 50.0}), "nvidia": Meets({"GPU Core": 55.0})},
    )

    await controller._tick()

    assert controller.source_states == {"aida64": SourceState.OK, "nvidia": SourceState.OK}


async def test_the_gui_hears_the_maximum_and_the_selection_behind_it():
    seen = []
    controller = make(
        {"aida64": ["CPU"], "nvidia": ["GPU Core"]}, {"aida64": Source({"CPU": 50.0}), "nvidia": Source()}
    )
    controller.set_callbacks(on_temps_change=lambda *args: seen.append(args))

    await controller._tick()

    max_temp, selection = seen[-1]
    assert max_temp == 50
    assert [(r.source, r.label, r.value) for r in selection.readings] == [
        ("aida64", "CPU", 50.0),
        ("nvidia", "GPU Core", None),
    ]


# --- a silent source -------------------------------------------------------------------------------


async def test_one_silent_source_among_two_turns_the_status_red_and_the_fan_follows_the_rest():
    controller = make(
        {"aida64": ["CPU"], "nvidia": ["GPU Core"]},
        {"aida64": Source(), "nvidia": Source({"GPU Core": 70.0})},
    )

    await controller._tick()

    assert not controller.sensors_ok
    assert controller.silent_sources == ["aida64"]
    assert controller.source_states == {"aida64": SourceState.SILENT, "nvidia": SourceState.OK}
    assert controller.device.writes == [70]
    assert not controller.fallback


async def test_a_source_that_raises_costs_nothing_but_its_own_readings(caplog):
    controller = make(
        {"lhm": ["CPU"], "nvidia": ["GPU Core"]},
        {"lhm": Source(raises=OSError("boom")), "nvidia": Source({"GPU Core": 66.0})},
    )

    with caplog.at_level(logging.ERROR):
        await controller._tick()

    assert controller.loop_ok, "a source's fault is not the loop's"
    assert controller.source_states["lhm"] is SourceState.SILENT
    assert not controller.sensors_ok
    assert controller.device.writes == [66]


async def test_a_source_that_needs_admin_rights_turns_the_status_red(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: False)
    controller = make(
        {"lenovo-wmi": ["CPU"], "nvidia": ["GPU Core"]},
        {"lenovo-wmi": Source(requires_admin=True), "nvidia": Source({"GPU Core": 45.0})},
    )

    await controller._tick()

    assert controller.source_states["lenovo-wmi"] is SourceState.NEEDS_ADMIN
    assert controller.silent_sources == ["lenovo-wmi"]
    assert not controller.sensors_ok


async def test_the_status_recovers_when_the_source_answers_again(caplog):
    silent = Source()
    controller = make({"aida64": ["CPU"]}, {"aida64": silent})
    await controller._tick()
    assert not controller.sensors_ok

    silent.readings = {"CPU": 50.0}
    with caplog.at_level(logging.INFO):
        await controller._tick()

    assert controller.sensors_ok
    assert "answering again" in caplog.text


# --- nothing to go on --------------------------------------------------------------------------------


async def test_when_no_tracked_sensor_has_a_reading_the_fan_runs_at_the_default_speed():
    controller = make({"aida64": ["CPU"]}, {"aida64": Source()}, fan=90)

    await controller._tick()

    assert controller.fallback
    assert controller.device.writes == [DEFAULT_SPEED]
    assert controller.monitoring
    assert not controller.sensors_ok


async def test_a_tracked_sensor_its_source_does_not_report_is_no_reading_either():
    controller = make({"aida64": ["Renamed"]}, {"aida64": Source({"CPU": 60.0})}, fan=90)

    await controller._tick()

    assert controller.fallback
    assert controller.sensors_ok, "the source answers; it is the sensor that is gone"
    assert controller.device.writes == [DEFAULT_SPEED]


async def test_the_default_speed_is_the_configured_one(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "default_speed", 61)
    controller = make({"aida64": ["CPU"]}, {"aida64": Source()})

    await controller._tick()

    assert controller.device.writes == [61]


async def test_the_fan_leaves_the_default_speed_when_a_reading_comes_back():
    source = Source()
    controller = make({"aida64": ["CPU"]}, {"aida64": source})
    await controller._tick()

    source.readings = {"CPU": 20.0}
    await controller._tick()

    assert not controller.fallback
    assert controller.device.writes == [DEFAULT_SPEED, 20]


async def test_a_tick_with_no_reading_does_not_feed_the_smoother(monkeypatch):
    """A default speed says nothing about the temperature, so no 0 °C may pile up in the median."""
    monkeypatch.setattr(CONFIG.control, "temp_window", 5)
    source = Source({"CPU": 80.0})
    controller = make({"aida64": ["CPU"]}, {"aida64": source})
    await controller._tick()

    source.readings = {}
    await controller._tick()
    await controller._tick()

    assert controller._smoother.samples == (80.0,)
    assert controller.max_temp == 80, "the last temperature stays what the status shows"


async def test_when_every_source_with_a_tracked_sensor_sleeps_the_curve_reads_its_floor():
    """A sleeping GPU is cold, not broken: the curve's floor is right for it, not the default speed."""
    controller = make({"nvidia": ["GPU Core"]}, {"nvidia": Source(asleep=True)}, fan=60)

    await controller._tick()

    assert not controller.fallback
    assert controller.max_temp == 0
    assert controller.device.writes == [0]
    assert controller.sensors_ok, "a sleeping GPU counts as answering"


async def test_a_sleeping_source_does_not_hold_back_one_that_has_a_reading():
    controller = make(
        {"nvidia": ["GPU Core"], "lenovo-wmi": ["CPU"]},
        {"nvidia": Source(asleep=True), "lenovo-wmi": Source({"CPU": 48.0})},
    )

    await controller._tick()

    assert controller.max_temp == 48
    assert controller.device.writes == [48]
    assert controller.sensors_ok


async def test_a_sleeping_source_next_to_a_silent_one_is_not_a_reason_for_the_floor():
    """Only when every source with a tracked sensor sleeps is nothing wrong; a silent one is."""
    controller = make(
        {"nvidia": ["GPU Core"], "aida64": ["CPU"]},
        {"nvidia": Source(asleep=True), "aida64": Source()},
        fan=60,
    )

    await controller._tick()

    assert controller.fallback
    assert controller.device.writes == [DEFAULT_SPEED]
    assert not controller.sensors_ok


async def test_a_source_with_nothing_tracked_is_not_counted_when_deciding_that_all_sleep():
    controller = make(
        {"nvidia": ["GPU Core"], "aida64": []},
        {"nvidia": Source(asleep=True), "aida64": Source()},
        fan=60,
    )

    await controller._tick()

    assert not controller.fallback
    assert controller.max_temp == 0
    assert controller.device.writes == [0]


# --- no sensor tracked at all ------------------------------------------------------------------------


@pytest.mark.parametrize("tracked", [{}, {"aida64": []}])
async def test_with_no_sensor_tracked_nothing_is_monitored_and_the_fan_runs_at_the_default_speed(tracked):
    source = Source({"CPU": 90.0})
    controller = make(tracked, {"aida64": source}, fan=100)

    await controller._tick()

    assert not controller.monitoring
    assert controller.fallback
    assert controller.device.writes == [DEFAULT_SPEED]
    assert controller.sensors_ok, "nothing is tracked, so nothing can be silent"


async def test_tracking_a_sensor_starts_monitoring_at_the_next_sync():
    controller = make({}, {"aida64": Source({"CPU": 52.0})})
    await controller._tick()
    assert not controller.monitoring

    CONFIG.sensors.tracked = {"aida64": ["CPU"]}
    controller.sync_sources()
    await controller._tick()

    assert controller.monitoring
    assert controller.max_temp == 52


# --- Stop, shutdown and Manual mode ---------------------------------------------------------------------


async def wait_for(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_stop_sets_the_default_speed_not_zero():
    controller = make({"aida64": ["CPU"]}, {"aida64": Source({"CPU": 80.0})})
    await controller.start()
    try:
        await wait_for(lambda: 80 in controller.device.writes)
    finally:
        await controller.stop()

    assert controller.device.writes[-1] == DEFAULT_SPEED
    assert controller.current_speed == DEFAULT_SPEED


async def test_shutdown_leaves_the_fan_at_the_default_speed_and_closes_the_device():
    controller = make({"aida64": ["CPU"]}, {"aida64": Source({"CPU": 80.0})})
    await controller.start()
    try:
        await wait_for(lambda: 80 in controller.device.writes)
    finally:
        await controller.shutdown()

    assert controller.device.writes[-1] == DEFAULT_SPEED
    assert not controller.connected


async def test_an_exit_after_a_stop_or_without_a_start_still_leaves_the_default_speed():
    """The app's exit sets the default speed whatever state the loop is in, a stopped one included."""
    controller = make({"aida64": ["CPU"]}, {"aida64": Source({"CPU": 80.0})})
    controller.mode = Mode.MANUAL
    controller.manual_speed = 12
    await controller._tick()  # a hand-set speed that a not-running loop would otherwise leave behind

    await controller.shutdown()

    assert controller.device.writes == [12, DEFAULT_SPEED]


async def test_manual_mode_runs_the_chosen_speed_whatever_the_sensors_or_the_default_say():
    controller = make({"aida64": ["CPU"]}, {"aida64": Source()})
    controller.mode = Mode.MANUAL
    controller.manual_speed = 12

    await controller._tick()

    assert controller.device.writes == [12]
