"""Reconfiguring the controller while it runs.

The settings panel can change the sensor source, the smoothing window and the serial port without a
restart. Each of those is read somewhere the configuration object cannot reach on its own, so each
needs code -- and a test that the code is what actually runs.
"""

from dataclasses import fields
from typing import ClassVar

import pytest

from iets_speed_control.controller import SpeedController
from iets_speed_control.entities.dimmer import Dimmer
from iets_speed_control.sensors import Aida64Provider, LibreHardwareMonitorWebProvider
from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG


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
    return SpeedController(sensor_provider=FakeProvider())


# --- the device is built from the configuration, not from the import ------------------------------


def test_a_new_device_uses_the_current_port(clean_config):
    """Binding CONFIG in the signature default would freeze the port at import time."""
    CONFIG.device.port = "COM42"

    assert Dimmer().port == "COM42"


def test_a_new_device_uses_the_current_baudrate_timeout_and_command(clean_config):
    CONFIG.device.baudrate = 9600
    CONFIG.device.timeout = 0.7
    CONFIG.device.pwm_command = "Channel1"

    device = Dimmer()

    assert (device.baudrate, device.timeout, device.dimmer_command) == (9600, 0.7, "Channel1")


def test_explicit_arguments_still_win(clean_config):
    CONFIG.device.port = "COM42"

    assert Dimmer(port="COM9").port == "COM9"


# --- swapping the sensor source ------------------------------------------------------------------


def test_the_source_can_be_swapped_by_name(controller):
    controller.sensors = "aida64"
    assert isinstance(controller.sensors, Aida64Provider)

    controller.sensors = "lhm-web"
    assert isinstance(controller.sensors, LibreHardwareMonitorWebProvider)


def test_swapping_the_source_starts_the_smoothers_over(controller):
    controller._cpu_smoother.add(90)
    controller._gpu_smoother.add(90)

    controller.sensors = "aida64"

    assert controller._cpu_smoother.samples == (), "readings from the old source say nothing about the new one"
    assert controller._gpu_smoother.samples == ()


def test_swapping_to_the_same_object_changes_nothing(controller):
    controller._cpu_smoother.add(70)
    same = controller.sensors

    controller.sensors = same

    assert controller._cpu_smoother.samples == (70.0,)


def test_an_unknown_source_is_refused(controller):
    before = controller.sensors

    with pytest.raises(ValueError, match="Unknown sensors.provider"):
        controller.sensors = "nonesuch"

    assert controller.sensors is before


# --- resizing the smoothing window ---------------------------------------------------------------


def test_resizing_the_window_replaces_both_smoothers(controller):
    before = (controller._cpu_smoother, controller._gpu_smoother)

    controller.temp_window = 9

    assert controller.temp_window == 9
    assert controller._cpu_smoother is not before[0]
    assert controller._gpu_smoother is not before[1]
    assert (controller._cpu_smoother.window, controller._gpu_smoother.window) == (9, 9)


def test_the_same_window_keeps_the_history(controller):
    controller._cpu_smoother.add(65)

    controller.temp_window = controller.temp_window

    assert controller._cpu_smoother.samples == (65.0,)


def test_a_window_below_one_is_clamped(controller):
    controller.temp_window = 0

    assert controller.temp_window == 1


def test_the_window_takes_effect_on_the_next_reading(controller):
    controller.temp_window = 1
    controller._cpu_smoother.add(50)

    assert controller._cpu_smoother.add(90) == 90, "a window of 1 must not average anything in"


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

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        self.connected = False
        self.disconnected = True


@pytest.fixture
def fake_device(monkeypatch):
    FakeDevice.instances = []
    monkeypatch.setattr("iets_speed_control.controller.Dimmer", FakeDevice)
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
