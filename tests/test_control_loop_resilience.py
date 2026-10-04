"""An unexpected error costs one tick, never the control loop.

The loop used to sit inside a single try: the first stray exception ended it for good, with the fan
left at its last speed and `running` still true, so nothing short of restarting the app brought it
back. These tests break ticks on purpose and watch what follows. They wait for events with a timeout
rather than sleeping a fixed time, so a slow runner makes them slower, not flaky.
"""

import asyncio
import logging
import threading

import pytest
from test_control_smoothing import FakeDevice, ScriptedSensors

from iets_speed_control import controller as controller_module
from iets_speed_control.controller import SpeedController
from iets_speed_control.util.config import CONFIG


class FlakyDevice(FakeDevice):
    """Fails the first `failures` reads with an error nobody planned for, then behaves."""

    baudrate = 115200  # reconnect() logs it

    def __init__(self, value: int = 0, failures: int = 1, connected: bool = True):
        super().__init__(value)
        self.failures = failures
        self.failed = 0
        self.connected = connected  # a freshly built Dimmer is not connected until connect()

    async def connect(self):
        self.connected = True
        return True

    async def read_dimmer_value(self):
        if self.failures:
            self.failures -= 1
            self.failed += 1
            raise RuntimeError("unexpected")
        return await super().read_dimmer_value()


class BrokenSensors:
    name = "broken"

    def __init__(self):
        self.calls = 0

    def get_temperatures(self):
        self.calls += 1
        raise OSError("WMI is not answering")


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
    monkeypatch.setattr(controller_module, "RETRY_DELAY", 0.01)


@pytest.fixture
async def started():
    """Start controllers for a test and stop them afterwards, even when an assertion failed."""
    controllers = []

    async def start(controller):
        controllers.append(controller)
        await controller.start()
        return controller

    yield start

    for controller in controllers:
        await controller.stop()


async def wait_until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def make_controller(device, sensors=None):
    controller = SpeedController(sensor_provider=sensors or ScriptedSensors([90]))
    controller.device = device
    return controller


async def test_the_loop_keeps_going_after_a_tick_raises(fast_loop, started):
    controller = await started(make_controller(FlakyDevice(failures=1)))

    await wait_until(lambda: 100 in controller.device.writes)

    assert controller.device.failed == 1, "test setup: the first tick must have failed"
    assert controller._loop_task is not None and not controller._loop_task.done(), "the loop ended"


async def test_a_lasting_fault_is_logged_once_with_its_traceback(fast_loop, started, caplog):
    with caplog.at_level(logging.ERROR):
        controller = await started(make_controller(FlakyDevice(failures=1000)))
        await wait_until(lambda: controller.device.failed >= 5)

    errors = [r for r in caplog.records if "Error in control loop" in r.message]
    assert len(errors) == 1, f"five failed ticks, {len(errors)} error lines"
    assert errors[0].exc_info, "the first report has to carry the traceback"


async def test_recovery_is_announced(fast_loop, started, caplog):
    with caplog.at_level(logging.INFO):
        await started(make_controller(FlakyDevice(failures=2)))
        await wait_until(lambda: any("working again" in r.message for r in caplog.records))


async def test_recovery_turns_the_status_back(fast_loop, started):
    seen = []
    controller = make_controller(FlakyDevice(failures=2))
    await started(controller)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args[0]))

    await wait_until(lambda: False in seen and seen[-1] is True)

    assert controller.connected


async def test_a_failing_tick_shows_as_disconnected(fast_loop, started):
    """Both the tray, which goes by status events, and Settings, which reads `connected`."""
    seen = []
    controller = make_controller(FlakyDevice(failures=1000))
    await started(controller)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args[0]))

    await wait_until(lambda: controller.device.failed >= 3)

    assert seen and seen[-1] is False, f"the tray was left thinking all is well: {seen}"
    assert not controller.connected, "Settings reads this, and no fan is being driven"


async def test_a_reconnect_during_a_fault_does_not_leave_the_tray_green(fast_loop, started, monkeypatch):
    """Reconnect reports the link up; the next failing tick has to take that back."""
    monkeypatch.setattr(controller_module, "Dimmer", lambda: FlakyDevice(failures=1000, connected=False))
    seen = []
    controller = make_controller(FlakyDevice(failures=1000))
    await started(controller)
    await wait_until(lambda: controller.device.failed >= 1)
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args[0]))

    await controller.reconnect()
    assert seen and seen[-1] is True, "test setup: Reconnect must have reported the link up"
    await wait_until(lambda: controller.device.failed >= 2)

    assert seen[-1] is False, f"the tray stayed green while ticks kept failing: {seen}"
    assert not controller.connected


async def test_a_sensor_error_is_neither_a_loop_error_nor_a_recovery(fast_loop, started, caplog):
    """The source reports its own outage; the loop must not add a second story on top."""
    sensors = BrokenSensors()

    with caplog.at_level(logging.INFO):
        controller = await started(make_controller(FakeDevice(), sensors))
        await wait_until(lambda: sensors.calls >= 3)

    messages = [r.message for r in caplog.records]
    assert not any("Error in control loop" in m for m in messages)
    assert not any("working again" in m for m in messages)
    assert not controller.sensors_ok


class SensorsThatBreakAfterOneRead:
    """Answers once, then fails like a source that went away."""

    name = "breaking"

    def __init__(self):
        self.calls = 0

    def get_temperatures(self):
        self.calls += 1
        if self.calls > 1:
            raise OSError("WMI is not answering")
        return {"CPU Package": 90.0}


class SensorsThatPause:
    """Holds the second read open until released, so a test can look at the controller mid-tick."""

    name = "pausing"

    def __init__(self):
        self.calls = 0
        self.paused = threading.Event()
        self.release = threading.Event()

    def get_temperatures(self):
        self.calls += 1
        if self.calls == 2:
            self.paused.set()
            self.release.wait(timeout=5)
        return {"CPU Package": 90.0}


async def test_settings_never_sees_the_link_up_in_the_middle_of_a_failing_streak(fast_loop, started):
    """A tick reads the sensors before it reaches the device and fails; meanwhile `connected` must stay False."""
    sensors = SensorsThatPause()
    controller = await started(make_controller(FlakyDevice(failures=1000), sensors))
    try:
        await wait_until(sensors.paused.is_set)

        assert controller.device.failed == 1, "test setup: the first tick must have failed"
        assert not controller.connected, "Settings would show a connection that drives nothing"
    finally:
        sensors.release.set()


async def test_a_tick_that_reads_no_temperatures_is_not_a_recovery(fast_loop, started, caplog):
    """After a tick fault, a tick that bails out at the sensors has not driven the fan."""
    sensors = SensorsThatBreakAfterOneRead()

    with caplog.at_level(logging.INFO):
        controller = await started(make_controller(FlakyDevice(failures=1), sensors))
        await wait_until(lambda: sensors.calls >= 4)

    assert controller.device.failed == 1, "test setup: the first tick must have failed at the device"
    assert not any("working again" in r.message for r in caplog.records)


async def test_after_a_failed_tick_the_device_is_read_again(fast_loop, started):
    """The cached dimmer value says nothing about a device that was just in the middle of an error."""
    device = FakeDevice(value=0)
    controller = await started(make_controller(device))
    await wait_until(lambda: 100 in device.writes)
    reads_before = device.reads

    def fail_once(*_args):
        controller.set_callbacks()
        raise RuntimeError("temperature callback failed")

    controller.set_callbacks(on_temps_change=fail_once)

    await wait_until(lambda: device.reads > reads_before)


async def test_a_failing_status_callback_does_not_end_the_loop(fast_loop, started, caplog):
    """The status callback is GUI code; a raise in it must cost a retry, not the loop."""
    calls = []

    def callback(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("the GUI went away")

    controller = make_controller(FlakyDevice(failures=1))
    await started(controller)
    controller.set_callbacks(on_status_change=callback)

    with caplog.at_level(logging.ERROR):
        await wait_until(lambda: 100 in controller.device.writes)

    assert any("control loop itself" in r.message for r in caplog.records), "test setup: the callback must have raised"
    assert not controller._loop_task.done()
