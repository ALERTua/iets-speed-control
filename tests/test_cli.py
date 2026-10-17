"""The console entrypoint, driven against a fake device so no port is opened and no fan moves."""

import asyncio
import logging

import pytest
from conftest import FakeProvider
from test_control_smoothing import FakeDevice

from iets_speed_control.controller import SpeedController
from iets_speed_control.entrypoints import cli
from iets_speed_control.util.config import CONFIG


class UnpluggedDevice(FakeDevice):
    """Opens on connect(), as a real port does: the status callback the CLI logs from fires then."""

    connected = False

    async def connect(self):
        self.connected = True
        return True


@pytest.fixture
def fake_fan(monkeypatch):
    """The CLI builds its own controller; give it one that has a fake device and the sources of the test."""
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "default_speed", 37)
    built = []

    def build():
        controller = SpeedController(sources={"aida64": FakeProvider({"CPU": 80.0})})
        controller.device = UnpluggedDevice(0)
        built.append(controller)
        return controller

    monkeypatch.setattr(cli, "SpeedController", build)
    return built


async def run_cli_for(seconds: float):
    task = asyncio.create_task(cli._cli())
    await asyncio.sleep(seconds)
    task.cancel()
    await task


async def test_the_cli_builds_the_controller_without_arguments_and_follows_the_tracked_sensors(fake_fan):
    CONFIG.control.curve = [[0, 0], [100, 100]]
    CONFIG.sensors.tracked = {"aida64": ["CPU"]}

    await run_cli_for(0.2)

    assert 80 in fake_fan[0].device.writes


async def test_the_cli_leaves_the_fan_at_the_default_speed_when_it_ends(fake_fan, caplog):
    CONFIG.sensors.tracked = {"aida64": ["CPU"]}

    with caplog.at_level(logging.INFO):
        await run_cli_for(0.2)

    assert fake_fan[0].device.writes[-1] == 37
    assert "default speed, 37 %" in caplog.text


async def test_the_cli_says_so_when_no_sensor_is_tracked(fake_fan, caplog):
    """Otherwise it would announce a maximum of 0 °C, as if the machine were cold."""
    with caplog.at_level(logging.INFO):
        await run_cli_for(0.2)

    assert "No sensor is tracked" in caplog.text
    assert "Starting with max" not in caplog.text
    assert fake_fan[0].device.writes[0] == 37
