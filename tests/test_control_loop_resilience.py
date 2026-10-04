"""An unexpected error costs one tick, never the control loop.

The loop used to sit inside a single try: the first stray exception ended it for good, with the fan
left at its last speed and `running` still true, so nothing short of restarting the app brought it
back. These tests break one tick on purpose and watch the next ones.
"""

import asyncio
import logging

import pytest
from test_control_smoothing import FakeDevice, ScriptedSensors

from iets_speed_control.controller import SpeedController
from iets_speed_control.util.config import CONFIG


class FlakyDevice(FakeDevice):
    """Fails the first `failures` reads with an error nobody planned for, then behaves."""

    def __init__(self, value: int = 0, failures: int = 1):
        super().__init__(value)
        self.failures = failures

    async def read_dimmer_value(self):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("unexpected")
        return await super().read_dimmer_value()


@pytest.fixture
def fast_loop(monkeypatch):
    monkeypatch.setattr(CONFIG.control, "delay", 0.01)
    monkeypatch.setattr(CONFIG.control, "resync_every", 0)
    monkeypatch.setattr(CONFIG.control, "temp_window", 1)
    monkeypatch.setattr(CONFIG.control, "max_step", 100)
    monkeypatch.setattr(CONFIG.control, "ignore_less_than", 0)
    monkeypatch.setattr(CONFIG.control, "curve", [[40, 0], [90, 100]])
    monkeypatch.setattr(CONFIG.sensors, "cpu_filter", "CPU")
    monkeypatch.setattr(CONFIG.sensors, "gpu_filter", "GPU")


async def run_briefly(controller, seconds=0.1):
    await controller.start()
    await asyncio.sleep(seconds)
    await controller.stop()


async def test_the_loop_keeps_going_after_a_tick_raises(fast_loop):
    controller = SpeedController(sensor_provider=ScriptedSensors([90]))
    controller.device = FlakyDevice(value=0, failures=1)

    await controller.start()
    await asyncio.sleep(0.1)

    assert controller.device.failures == 0, "test setup: the first tick must have failed"
    assert 100 in controller.device.writes, "after the failed tick the fan still has to follow the curve"
    assert controller._loop_task is not None and not controller._loop_task.done(), "the loop ended"

    await controller.stop()


async def test_a_lasting_fault_is_logged_once_with_its_traceback(fast_loop, caplog):
    controller = SpeedController(sensor_provider=ScriptedSensors([90]))
    controller.device = FlakyDevice(failures=1000)

    with caplog.at_level(logging.ERROR):
        await run_briefly(controller)

    errors = [r for r in caplog.records if "Error in control loop" in r.message]
    assert controller.device.failures < 995, "test setup: several ticks must have failed"
    assert len(errors) == 1, f"one fault, {len(errors)} error lines"
    assert errors[0].exc_info, "the first report has to carry the traceback"


async def test_recovery_is_announced(fast_loop, caplog):
    controller = SpeedController(sensor_provider=ScriptedSensors([90]))
    controller.device = FlakyDevice(failures=2)

    with caplog.at_level(logging.INFO):
        await run_briefly(controller)

    assert any("working again" in r.message for r in caplog.records)


async def test_recovery_turns_the_status_back(fast_loop):
    seen = []
    controller = SpeedController(sensor_provider=ScriptedSensors([90]))
    controller.device = FlakyDevice(failures=2)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args))

    await controller.start()
    await asyncio.sleep(0.1)

    connected = [args[0] for args in seen]
    assert False in connected, f"test setup: the failure must have been reported: {connected}"
    assert connected[-1] is True, f"the GUI was never told the loop recovered: {connected}"

    await controller.stop()


async def test_a_failing_tick_shows_as_disconnected(fast_loop):
    """The tray turns red: the fan is not being driven while ticks fail."""
    seen = []
    controller = SpeedController(sensor_provider=ScriptedSensors([90]))
    controller.device = FlakyDevice(failures=1000)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args))

    await controller.start()
    await asyncio.sleep(0.05)

    assert not controller.connected
    assert any(args[0] is False for args in seen), f"the GUI never heard about it: {seen}"

    await controller.stop()
