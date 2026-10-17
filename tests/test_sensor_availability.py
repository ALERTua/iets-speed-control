"""A silent temperature source has to be visible, not silent.

Every provider reports "I cannot reach my app" by returning no readings rather than by raising, so
the control loop used to sail on with 0 °C and drive the fan from the curve's floor while the tray
icon stayed green. This is the seam that turns that into a red icon and a line on Home.
"""

import asyncio

import pytest
from conftest import controller_tracking
from test_control_smoothing import FakeDevice, ScriptedSensors

from iets_speed_control.util.config import CONFIG

ctk = pytest.importorskip("customtkinter")


class SilentSensors:
    """Reachable but answering with nothing, which is what an unreachable web server looks like."""

    name = "lhm-web"

    def __init__(self):
        self.calls = 0

    def get_temperatures(self):
        self.calls += 1
        return {}


class BrokenSensors:
    name = "aida64"

    def get_temperatures(self):
        raise OSError("WMI is not answering")


@pytest.fixture
def fast_loop(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "temp_window", 1)


async def run_briefly(controller, seconds=0.08):
    await controller.start()
    await asyncio.sleep(seconds)
    await controller.stop()


# --- the controller notices ------------------------------------------------------------------


def test_a_fresh_controller_assumes_the_source_works():
    """Nothing has been read yet, so there is nothing to complain about."""
    assert controller_tracking(SilentSensors(), "lhm-web").sensors_ok


async def test_a_source_that_answers_with_nothing_is_marked_unavailable(fast_loop):
    controller = controller_tracking(SilentSensors(), "lhm-web")
    controller.device = FakeDevice(0)

    await run_briefly(controller)

    assert controller.sources["lhm-web"].calls, "test setup: the loop must have read at least once"
    assert not controller.sensors_ok


async def test_a_source_that_raises_is_marked_unavailable(fast_loop):
    controller = controller_tracking(BrokenSensors())
    controller.device = FakeDevice(0)

    await run_briefly(controller)

    assert not controller.sensors_ok


async def test_a_working_source_stays_available(fast_loop):
    controller = controller_tracking(ScriptedSensors([60]))
    controller.device = FakeDevice(0)

    await run_briefly(controller)

    assert controller.sensors_ok


async def test_the_status_callback_fires_on_the_transition(fast_loop):
    seen = []
    controller = controller_tracking(SilentSensors(), "lhm-web")
    controller.device = FakeDevice(0)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args))

    await run_briefly(controller)

    assert any(args[2] is False for args in seen), f"the GUI never heard about it: {seen}"


async def test_only_the_transition_is_announced(fast_loop, caplog):
    """The loop ticks many times a second; one report per outage, not per tick."""
    import logging

    controller = controller_tracking(SilentSensors(), "lhm-web")
    controller.device = FakeDevice(0)

    with caplog.at_level(logging.ERROR):
        await run_briefly(controller, seconds=0.15)

    complaints = [r for r in caplog.records if "No temperatures from" in r.message]
    assert controller.sources["lhm-web"].calls > 3, "test setup: the loop must have ticked several times"
    assert len(complaints) == 1, f"one outage, {len(complaints)} log lines"


async def test_recovery_is_announced_too(fast_loop):
    seen = []
    controller = controller_tracking(SilentSensors(), "lhm-web")
    controller.device = FakeDevice(0)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args))

    await controller.start()
    await asyncio.sleep(0.05)
    controller._sources["lhm-web"] = ScriptedSensors([60])  # as if the web server came back
    await asyncio.sleep(0.05)
    await controller.stop()

    flags = [args[2] for args in seen]
    assert False in flags and flags[-1] is True, f"expected a drop and a recovery: {flags}"


# --- the tray icon and the status line ---------------------------------------------------------


class FakeShell:
    def __init__(self):
        self.healthy = []

    def update_tray_icon(self, healthy):
        self.healthy.append(healthy)


@pytest.fixture
def panel(tk_root):
    """Home with the lhm-web source tracked, as its last read left it: answering with nothing."""
    from iets_speed_control.gui.status import StatusPanel
    from iets_speed_control.util.tracking import SourceState

    shell = FakeShell()
    controller = controller_tracking(SilentSensors(), "lhm-web", ["CPU/Core Max"])
    controller._source_states = {"lhm-web": SourceState.SILENT}
    widget = StatusPanel(tk_root, controller, shell)
    widget.stop_polling()
    widget.shell = shell
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


def test_the_tray_goes_red_when_the_source_is_silent(panel):
    panel._apply_status(connected=True, running=True, sensors_ok=False)

    assert panel.shell.healthy == [False], "a silent source is exactly as useless as no device"


def test_the_tray_is_green_only_when_both_halves_work(panel):
    panel._apply_status(connected=True, running=True, sensors_ok=True)

    assert panel.shell.healthy == [True]


def test_the_status_line_names_the_silent_source(panel):
    panel._apply_status(connected=True, running=True, sensors_ok=False)

    assert "No temperatures from LibreHardwareMonitor (web server)" in panel.status_label.cget("text")


def test_the_status_line_says_to_restart_as_administrator(panel):
    from iets_speed_control.util.tracking import SourceState

    panel.controller._source_states = {"lenovo-wmi": SourceState.NEEDS_ADMIN}

    panel._apply_status(connected=True, running=True, sensors_ok=False)

    assert "restart as administrator" in panel.status_label.cget("text")


def test_a_source_that_needs_no_admin_rights_gets_no_such_advice(panel):
    panel._apply_status(connected=True, running=True, sensors_ok=False)

    assert "administrator" not in panel.status_label.cget("text")


def test_every_silent_source_is_named(panel):
    from iets_speed_control.util.tracking import SourceState

    panel.controller._source_states = {
        "aida64": SourceState.SILENT,
        "lhm-web": SourceState.OK,
        "lenovo-wmi": SourceState.NEEDS_ADMIN,
    }

    panel._apply_status(connected=True, running=True, sensors_ok=False)

    text = panel.status_label.cget("text")
    assert "No temperatures from AIDA64" in text
    assert "Lenovo Legion (WMI): restart as administrator" in text
    assert "LibreHardwareMonitor" not in text, "a source that answered is not part of the problem"


def test_nothing_tracked_keeps_the_tray_green(panel, monkeypatch):
    """Tracking nothing is a choice the user made, so it is no fault to raise."""
    monkeypatch.setattr(CONFIG.sensors, "tracked", {})
    panel.controller.sync_sources()

    panel._apply_status(connected=True, running=True, sensors_ok=panel.controller.sensors_ok)

    assert panel.shell.healthy == [True]
    assert "No temperatures" not in panel.status_label.cget("text")


def test_a_missing_device_still_reads_as_disconnected(panel):
    """Both can be wrong at once; the serial link is the one you fix first."""
    panel._apply_status(connected=False, running=True, sensors_ok=False)

    assert "Disconnected" in panel.status_label.cget("text")
    assert panel.shell.healthy == [False]


def test_a_failing_control_loop_turns_the_tray_red_and_says_so(panel):
    panel._apply_status(connected=True, running=True, sensors_ok=True, loop_ok=False)

    assert panel.shell.healthy == [False], "the fan is not being driven while ticks fail"
    assert "Control loop error" in panel.status_label.cget("text")
