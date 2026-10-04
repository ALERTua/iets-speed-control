"""The curve is evaluated at the hottest reading the active source's filters match."""

import asyncio

import pytest
from test_control_smoothing import FakeDevice

from iets_speed_control.controller import SpeedController
from iets_speed_control.util.config import CONFIG


class FixedSensors:
    name = "fixed"

    def get_temperatures(self):
        return {"CPU Package": 60.0, "GPU Core": 80.0, "SSD Composite": 95.0}


@pytest.fixture
def fast_loop(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "temp_window", 1)
    monkeypatch.setattr(CONFIG.control, "max_step", 100)
    monkeypatch.setattr(CONFIG.control, "ignore_less_than", 0)
    monkeypatch.setattr(CONFIG.control, "curve", [[0, 0], [100, 100]])


async def run_until_written(controller):
    await controller.start()
    try:
        async with asyncio.timeout(5):
            while not controller.device.writes:
                await asyncio.sleep(0.005)
    finally:
        await controller.stop()


async def test_the_defaults_follow_the_hotter_of_cpu_and_gpu(fast_loop):
    controller = SpeedController(sensor_provider=FixedSensors())
    controller.device = FakeDevice(0)

    await run_until_written(controller)

    assert controller.max_temp == 80, "the SSD is hotter but no default filter selects it"
    assert controller.device.writes[0] == 80
    assert controller.selection.hottest.pattern == "GPU"


async def test_the_sources_own_filters_drive_the_curve(fast_loop, monkeypatch):
    monkeypatch.setitem(CONFIG.sensors.filters, "fixed", ["ssd", "CPU"])
    controller = SpeedController(sensor_provider=FixedSensors())
    controller.device = FakeDevice(0)

    await run_until_written(controller)

    assert controller.max_temp == 95
    assert controller.selection.hottest.label == "SSD Composite"


async def test_another_sources_filters_are_ignored(fast_loop, monkeypatch):
    monkeypatch.setitem(CONFIG.sensors.filters, "someone-else", ["SSD"])
    controller = SpeedController(sensor_provider=FixedSensors())
    controller.device = FakeDevice(0)

    await run_until_written(controller)

    assert controller.max_temp == 80


async def test_the_gui_hears_the_maximum_and_which_filter_gave_it(fast_loop):
    seen = []
    controller = SpeedController(sensor_provider=FixedSensors())
    controller.device = FakeDevice(0)
    controller.set_callbacks(on_temps_change=lambda *args: seen.append(args))

    await run_until_written(controller)

    max_temp, selection = seen[-1]
    assert max_temp == 80
    assert [match.value for match in selection.matches] == [60.0, 80.0]
