"""Manual mode end to end: the slider in the settings panel through to the value on the wire.

Uses the fake device from the smoothing tests, so the real fan is never driven.
"""

import asyncio

import pytest
from test_control_smoothing import FakeDevice, ScriptedSensors

from iets_speed_control.controller import Mode, SpeedController
from iets_speed_control.util.config import CONFIG

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def fast_loop(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "temp_window", 1)
    monkeypatch.setattr(CONFIG.sensors, "cpu_filter", "CPU")
    monkeypatch.setattr(CONFIG.sensors, "gpu_filter", "GPU")


# --- the value reaches the device ----------------------------------------------------------------


async def test_manual_speed_is_written_to_the_device(fast_loop):
    controller = SpeedController(sensor_provider=ScriptedSensors([60]))
    controller.device = FakeDevice(0)
    controller.mode = Mode.MANUAL
    controller.manual_speed = 42

    await controller.start()
    await asyncio.sleep(0.08)
    await controller.stop()

    assert 42 in controller.device.writes, f"manual mode never sent the value: {controller.device.writes}"


async def test_moving_the_slider_mid_run_changes_what_is_sent(fast_loop):
    controller = SpeedController(sensor_provider=ScriptedSensors([60]))
    controller.device = FakeDevice(0)
    controller.mode = Mode.MANUAL
    controller.manual_speed = 20

    await controller.start()
    await asyncio.sleep(0.06)
    controller.manual_speed = 75
    await asyncio.sleep(0.06)
    await controller.stop()

    writes = controller.device.writes
    assert 20 in writes and 75 in writes, f"both settings should have reached the device: {writes}"
    assert writes.index(75) > writes.index(20)


async def test_manual_mode_ignores_the_temperature(fast_loop, monkeypatch):
    """A hot chip must not override an explicit manual setting; that is what manual means."""
    monkeypatch.setattr(CONFIG.control, "curve", [[0, 0], [100, 100]])

    controller = SpeedController(sensor_provider=ScriptedSensors([95]))
    controller.device = FakeDevice(0)
    controller.mode = Mode.MANUAL
    controller.manual_speed = 10

    await controller.start()
    await asyncio.sleep(0.08)
    running = list(controller.device.writes)
    await controller.stop()  # stopping parks the fan at 0, which is not part of what manual sent

    assert set(running) <= {10}, f"the curve leaked into manual mode: {running}"
    assert running, "manual mode sent nothing at all"


async def test_the_ramp_down_limit_does_not_apply_to_manual(fast_loop, monkeypatch):
    """max_step protects an automatic ramp-down; an explicit manual value is obeyed at once."""
    monkeypatch.setattr(CONFIG.control, "max_step", 3)

    controller = SpeedController(sensor_provider=ScriptedSensors([60]))
    controller.device = FakeDevice(90)
    controller.mode = Mode.MANUAL
    controller.manual_speed = 10

    await controller.start()
    await asyncio.sleep(0.08)
    await controller.stop()

    assert controller.device.writes[0] == 10, f"expected one step straight to 10, got {controller.device.writes}"


async def test_hysteresis_does_not_apply_to_manual(fast_loop, monkeypatch):
    """A small deliberate nudge must still take effect."""
    monkeypatch.setattr(CONFIG.control, "ignore_less_than", 20)

    controller = SpeedController(sensor_provider=ScriptedSensors([60]))
    controller.device = FakeDevice(50)
    controller.mode = Mode.MANUAL
    controller.manual_speed = 52

    await controller.start()
    await asyncio.sleep(0.08)
    await controller.stop()

    assert 52 in controller.device.writes, f"a 2 % manual change was swallowed: {controller.device.writes}"


# --- remembered between runs -----------------------------------------------------------------------


def test_the_configured_modes_match_the_controller_enum():
    """config.py cannot import the enum without a cycle, so the two lists are checked instead."""
    from iets_speed_control.util import config as cfg

    assert set(cfg.CONTROL_MODES) == {mode.value for mode in Mode}


def test_the_mode_is_read_from_the_configuration(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "mode", "manual")

    assert SpeedController(sensor_provider=ScriptedSensors([60])).mode is Mode.MANUAL


def test_changing_the_mode_updates_the_configuration():
    controller = SpeedController(sensor_provider=ScriptedSensors([60]))

    controller.mode = Mode.MANUAL

    assert CONFIG.control.mode == "manual", "otherwise Save writes the old mode"


def test_an_unknown_mode_is_rejected():
    from iets_speed_control.util import config as cfg

    config = cfg.defaults()
    config.control.mode = "cruise"

    with pytest.raises(cfg.ConfigError, match="control.mode must be one of"):
        cfg.validate(config)


def test_a_mode_is_read_case_insensitively():
    from iets_speed_control.util import config as cfg

    config = cfg.defaults()
    config.control.mode = "Manual"

    assert cfg.validate(config).control.mode == "manual"


def test_the_manual_speed_is_read_from_the_configuration(monkeypatch):
    """A fresh run must resume where the last one left off instead of at 0 %."""
    monkeypatch.setattr(CONFIG.control, "manual_speed", 58)

    assert SpeedController(sensor_provider=ScriptedSensors([60])).manual_speed == 58


def test_setting_the_manual_speed_updates_the_configuration():
    controller = SpeedController(sensor_provider=ScriptedSensors([60]))

    controller.manual_speed = 47

    assert CONFIG.control.manual_speed == 47, "otherwise Save writes the old value"


def test_a_clamped_manual_speed_is_what_gets_stored():
    controller = SpeedController(sensor_provider=ScriptedSensors([60]))

    controller.manual_speed = 150

    assert controller.manual_speed == 100
    assert CONFIG.control.manual_speed == 100, "the file must not hold a value the app would refuse"


def test_only_a_changed_manual_speed_is_written():
    from iets_speed_control.util import config as cfg

    config = cfg.defaults()
    config.control.manual_speed = 58

    assert cfg.to_sparse(config) == {"control": {"manual_speed": 58}}


def test_a_manual_speed_outside_the_range_is_rejected():
    from iets_speed_control.util import config as cfg

    config = cfg.defaults()
    config.control.manual_speed = 120

    with pytest.raises(cfg.ConfigError, match="control.manual_speed must be within 0-100"):
        cfg.validate(config)


# --- the slider in the panel ---------------------------------------------------------------------
#
# These drive the widget commands directly rather than synthesizing clicks, so the Manual card does
# not need to be the visible section. `view` is the shared panel from conftest.


def set_manual(view, speed):
    """Move the slider the way a drag does: set() alone does not fire the command."""
    view.manual_slider.set(speed)
    view.manual_slider.cget("command")(speed)


def type_manual(view, text):
    view.manual_value.delete(0, "end")
    view.manual_value.insert(0, text)
    view._apply_manual_entry()


def test_the_control_is_actually_on_screen(view, tk_root):
    """It was built on the card and packed in_ the holder, so the holder covered it: mapped, invisible."""
    view.show("Manual")
    view.update()

    assert view.manual_slider.winfo_ismapped()
    assert view.manual_slider.master is view.manual_row.control, "it has to be a child of the holder, not a sibling"
    assert view.manual_value.master is view.manual_row.control
    assert view.manual_slider.winfo_width() > 50


def test_the_slider_sets_the_manual_speed(view):
    set_manual(view, 65)

    assert view.controller.manual_speed == 65
    assert view.manual_value.get() == "65"


def test_the_slider_shows_whole_percent(view):
    """The controller already rounds what goes on the wire; the readout is the panel's own job."""
    set_manual(view, 65.7)

    assert view.manual_value.get() == "65"
    assert view.controller.manual_speed == 65


def test_an_exact_value_can_be_typed(view):
    type_manual(view, "63")

    assert view.controller.manual_speed == 63
    assert view.manual_slider.get() == 63, "the slider has to follow what was typed"


def test_a_typed_value_out_of_range_is_clamped(view):
    type_manual(view, "150")

    assert view.controller.manual_speed == 100
    assert view.manual_value.get() == "100"


def test_a_typed_percent_sign_is_tolerated(view):
    type_manual(view, "55 %")

    assert view.controller.manual_speed == 55


def test_a_typed_nonsense_value_reverts(view):
    set_manual(view, 40)

    type_manual(view, "fast")

    assert view.controller.manual_speed == 40
    assert view.manual_value.get() == "40", "the box must show what is actually set"


def test_the_control_stays_usable_in_auto_mode(view):
    """Setting a speed while in Auto is allowed: it is remembered for when Manual is selected."""
    view.mode_switch.cget("command")("Auto")

    set_manual(view, 44)

    assert view.manual_slider.cget("state") == "normal"
    assert view.manual_value.cget("state") == "normal"
    assert view.controller.manual_speed == 44


def test_switching_to_manual_applies_the_remembered_speed(view):
    """The remembered value is the user's own choice; the current automatic speed must not win."""
    set_manual(view, 63)
    view.controller._current_speed = 70  # what the curve happens to be asking for right now

    view.mode_switch.set("Manual")
    view.mode_switch.cget("command")("Manual")

    assert view.controller.mode == Mode.MANUAL
    assert view.controller.manual_speed == 63
    assert view.manual_slider.get() == 63, "and the slider must show it"


def test_the_mode_switch_reflects_a_change_made_elsewhere(view):
    """The tray menu and the Home panel change the same mode; the panel must not show a stale one."""
    view.controller.mode = Mode.MANUAL

    view.sync_mode()

    assert view.mode_switch.get() == "Manual"
