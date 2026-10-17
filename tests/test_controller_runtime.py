"""Reconfiguring the controller while it runs.

The settings panel can change the tracked sensors, the smoothing window and the serial port without a
restart. Each of those is read somewhere the configuration object cannot reach on its own, so each
needs code -- and a test that the code is what actually runs.
"""

from dataclasses import fields
from typing import ClassVar

import pytest

from iets_speed_control.controller import SpeedController
from iets_speed_control.entities.tasmota_fan import TasmotaSerialFan
from iets_speed_control.sensors import LibreHardwareMonitorWebProvider
from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG
from iets_speed_control.util.tracking import SourceState


@pytest.fixture
def clean_config():
    saved = cfg.copy(CONFIG)
    try:
        yield CONFIG
    finally:
        for section in fields(CONFIG):
            setattr(CONFIG, section.name, getattr(saved, section.name))


class FakeProvider:
    name = "fake"

    def get_temperatures(self):
        return {"CPU": 50.0, "GPU": 40.0}


@pytest.fixture
def controller():
    CONFIG.sensors.tracked = {"aida64": ["CPU"]}
    return SpeedController(sources={"aida64": FakeProvider()})


# --- the device is built from the configuration, not from the import ------------------------------


def test_a_new_device_uses_the_current_port(clean_config):
    """Binding CONFIG in the signature default would freeze the port at import time."""
    CONFIG.device.port = "COM42"

    assert TasmotaSerialFan().port == "COM42"


def test_a_new_device_uses_the_current_baudrate_timeout_and_command(clean_config):
    CONFIG.device.baudrate = 9600
    CONFIG.device.timeout = 0.7
    CONFIG.device.pwm_command = "Channel1"

    device = TasmotaSerialFan()

    assert (device.baudrate, device.timeout, device.dimmer_command) == (9600, 0.7, "Channel1")


def test_explicit_arguments_still_win(clean_config):
    CONFIG.device.port = "COM42"

    assert TasmotaSerialFan(port="COM9").port == "COM9"


# --- changing the tracked sources ----------------------------------------------------------------


def test_a_source_that_is_given_stands_in_for_its_code_name(controller):
    assert isinstance(controller.sources["aida64"], FakeProvider)


def test_a_new_tracked_source_is_created_by_its_code_name(controller):
    CONFIG.sensors.tracked = {"aida64": ["CPU"], "lhm-web": []}

    controller.sync_sources()

    assert isinstance(controller.sources["lhm-web"], LibreHardwareMonitorWebProvider)


def test_a_source_that_stays_keeps_its_instance(controller):
    """A source holds its own state (a logged warning, a found GPU), which an edit elsewhere must not cost."""
    CONFIG.sensors.tracked = {"aida64": ["CPU"], "lhm-web": []}
    controller.sync_sources()
    given, created = controller.sources["aida64"], controller.sources["lhm-web"]

    CONFIG.sensors.tracked = {"aida64": ["CPU"], "lhm-web": ["Core Max"], "nvidia": []}
    controller.sync_sources()

    assert controller.sources["aida64"] is given
    assert controller.sources["lhm-web"] is created


def test_a_source_that_is_removed_is_dropped(controller):
    CONFIG.sensors.tracked = {"lhm-web": []}

    controller.sync_sources()

    assert list(controller.sources) == ["lhm-web"]


def test_changing_the_sources_starts_the_smoothers_over(controller):
    controller._smoother.add(90)
    CONFIG.sensors.tracked = {"aida64": ["CPU"], "lhm-web": []}

    controller.sync_sources()

    assert controller._smoother.samples == (), "readings of the old set say nothing about the new one"


def test_an_unchanged_set_keeps_the_smoothing_history(controller):
    controller._smoother.add(70)

    controller.sync_sources()

    assert controller._smoother.samples == (70.0,)


def test_a_removed_source_takes_its_verdict_with_it(controller):
    """A source that is gone cannot be silent, so it must not keep the tray red."""
    controller._source_states = {"aida64": SourceState.SILENT}
    controller._sensors_ok = False
    CONFIG.sensors.tracked = {"lhm-web": []}

    controller.sync_sources()

    assert controller.sensors_ok
    assert controller.source_states == {}


# --- resizing the smoothing window ---------------------------------------------------------------


def test_resizing_the_window_replaces_the_smoother(controller):
    before = controller._smoother

    controller.temp_window = 9

    assert controller.temp_window == 9
    assert controller._smoother is not before
    assert controller._smoother.window == 9


def test_the_same_window_keeps_the_history(controller):
    controller._smoother.add(65)

    controller.temp_window = controller.temp_window

    assert controller._smoother.samples == (65.0,)


def test_a_window_below_one_is_clamped(controller):
    controller.temp_window = 0

    assert controller.temp_window == 1


def test_the_window_takes_effect_on_the_next_reading(controller):
    controller.temp_window = 1
    controller._smoother.add(50)

    assert controller._smoother.add(90) == 90, "a window of 1 must not average anything in"


# --- reconnecting --------------------------------------------------------------------------------


class FakeDevice:
    """Stands in for the serial device: records the calls instead of opening a port."""

    instances: ClassVar[list[FakeDevice]] = []

    def __init__(self, port=None, baudrate=None, timeout=None, dimmer_command=None):
        self.port = port or CONFIG.device.port
        self.baudrate = baudrate or CONFIG.device.baudrate
        self.timeout = timeout or CONFIG.device.timeout
        self.dimmer_command = dimmer_command or CONFIG.device.pwm_command
        self.connected = False
        self.disconnected = False
        FakeDevice.instances.append(self)

    def describe(self):
        return f"{self.port} at {self.baudrate} baud"

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        self.connected = False
        self.disconnected = True


@pytest.fixture
def fake_device(monkeypatch):
    FakeDevice.instances = []
    monkeypatch.setattr("iets_speed_control.controller.create_fan", FakeDevice)
    return FakeDevice


async def test_reconnect_builds_a_device_from_the_current_configuration(controller, fake_device, clean_config):
    controller.device = fake_device()
    controller.device.connected = True
    old = controller.device
    CONFIG.device.port = "COM42"
    CONFIG.device.baudrate = 9600

    assert await controller.reconnect()

    assert old.disconnected, "the old port must be released before the new one is opened"
    assert controller.device is not old
    assert (controller.device.port, controller.device.baudrate) == ("COM42", 9600)
    assert controller.connected


async def test_reconnect_forgets_the_cached_dimmer_value(controller, fake_device):
    controller.device = fake_device()
    controller._last_sent = 55
    controller._ticks_since_resync = 12

    await controller.reconnect()

    assert controller._last_sent is None, "the previous device's value says nothing about this one"
    assert controller._ticks_since_resync == 0


async def test_reconnect_works_when_nothing_was_connected(controller, fake_device):
    controller.device = fake_device()

    assert await controller.reconnect()
    assert controller.device.connected


async def test_reconnect_reports_status(controller, fake_device):
    seen = []
    controller.set_callbacks(on_status_change=lambda connected, *_rest: seen.append(connected))
    controller.device = fake_device()

    await controller.reconnect()

    assert seen, "the GUI only learns about the drop and the recovery through the callback"
    assert seen[0] is False and seen[-1] is True
