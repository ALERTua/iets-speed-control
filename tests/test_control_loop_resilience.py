"""An unexpected error costs one tick, never the control loop.

The loop used to sit inside a single try: the first stray exception ended it for good, with the fan
left at its last speed and `running` still true, so nothing short of restarting the app brought it
back. These tests break ticks on purpose and watch what follows. They wait for events with a timeout
rather than sleeping a fixed time, so a slow runner makes them slower, not flaky.

A tick fault is reported on its own (`loop_ok`), apart from the serial link (`connected`) and the
temperature source (`sensors_ok`): a fault in the GUI or the sensors says nothing about the port.
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
        controller.set_callbacks()  # a test may leave a callback that raises; stop() reports status too
        await controller.stop()


async def wait_until(predicate, describe=None, timeout=5.0):
    """Wait for `predicate`; on a timeout, say what was awaited and what `describe` saw last."""
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)
    except TimeoutError:
        seen = f"; last seen: {describe()}" if describe else ""
        raise AssertionError(
            f"timed out after {timeout} s waiting for {predicate.__doc__ or predicate}{seen}"
        ) from None


def make_controller(device, sensors=None):
    controller = SpeedController(sensor_provider=sensors or ScriptedSensors([90]))
    controller.device = device
    return controller


def record_status(controller):
    """Collect the status events: (connected, running, sensors_ok, loop_ok)."""
    seen = []
    controller.set_callbacks(on_status_change=lambda *args: seen.append(args))
    return seen


async def test_the_loop_keeps_going_after_a_tick_raises(fast_loop, started):
    controller = await started(make_controller(FlakyDevice(failures=1)))

    await wait_until(lambda: 100 in controller.device.writes, lambda: controller.device.writes)

    assert controller.device.failed == 1, "test setup: the first tick must have failed"
    assert controller._loop_task is not None and not controller._loop_task.done(), "the loop ended"


async def test_a_lasting_fault_is_logged_once_with_its_traceback(fast_loop, started, caplog):
    with caplog.at_level(logging.ERROR):
        controller = await started(make_controller(FlakyDevice(failures=1000)))
        await wait_until(lambda: controller.device.failed >= 5, lambda: controller.device.failed)

    errors = [r for r in caplog.records if "Error in control loop" in r.message]
    assert len(errors) == 1, f"five failed ticks, {len(errors)} error lines"
    assert errors[0].exc_info, "the first report has to carry the traceback"


async def test_recovery_is_announced(fast_loop, started, caplog):
    with caplog.at_level(logging.INFO):
        await started(make_controller(FlakyDevice(failures=2)))
        await wait_until(lambda: any("working again" in r.message for r in caplog.records))


async def test_a_failing_tick_is_reported_apart_from_the_link(fast_loop, started):
    """The tray goes red for the fault, while Settings still shows the serial link that is up."""
    controller = make_controller(FlakyDevice(failures=1000))
    await started(controller)
    seen = record_status(controller)

    await wait_until(lambda: controller.device.failed >= 3, lambda: controller.device.failed)

    assert not controller.loop_ok
    assert controller.connected, "the port answers; it is the tick that fails"
    assert seen and seen[-1][3] is False, f"the tray was never told about the fault: {seen}"


async def test_recovery_turns_the_status_back(fast_loop, started):
    controller = make_controller(FlakyDevice(failures=2))
    await started(controller)
    seen = record_status(controller)

    await wait_until(lambda: any(args[3] is False for args in seen) and seen[-1][3] is True, lambda: seen)

    assert controller.loop_ok


async def test_a_reconnect_during_a_fault_keeps_the_fault_visible(fast_loop, started, monkeypatch):
    """Reconnect reports the link; the fault stays in the same report, so the tray stays red."""
    monkeypatch.setattr(controller_module, "Dimmer", lambda: FlakyDevice(failures=1000, connected=False))
    controller = make_controller(FlakyDevice(failures=1000))
    await started(controller)
    await wait_until(lambda: not controller.loop_ok)
    seen = record_status(controller)

    await controller.reconnect()
    await wait_until(lambda: controller.device.failed >= 2, lambda: controller.device.failed)

    assert seen and all(args[3] is False for args in seen), f"the fault dropped out of a report: {seen}"
    assert seen[-1][0] is True, "the new port is up, and the status says so"


async def test_a_sensor_error_is_not_a_loop_error(fast_loop, started, caplog):
    """The source reports its own outage; the loop must not add a second story on top."""
    sensors = BrokenSensors()

    with caplog.at_level(logging.INFO):
        controller = await started(make_controller(FakeDevice(), sensors))
        await wait_until(lambda: sensors.calls >= 3, lambda: sensors.calls)

    assert not any("Error in control loop" in r.message for r in caplog.records)
    assert not controller.sensors_ok
    assert controller.loop_ok


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


async def test_a_tick_fault_ends_at_the_next_clean_tick_even_without_temperatures(fast_loop, started):
    """The loop works again; that the source has no temperatures is reported as a sensor problem."""
    sensors = SensorsThatBreakAfterOneRead()
    controller = await started(make_controller(FlakyDevice(failures=1), sensors))

    await wait_until(lambda: sensors.calls >= 3, lambda: sensors.calls)

    assert controller.device.failed == 1, "test setup: the first tick must have failed at the device"
    assert controller.loop_ok
    assert not controller.sensors_ok


async def test_after_a_device_fault_the_device_is_read_again(fast_loop, started):
    """The cached dimmer value says nothing about a device that failed in the middle of an exchange."""
    device = FakeDevice(value=0)
    controller = await started(make_controller(device))
    await wait_until(lambda: 100 in device.writes, lambda: device.writes)
    reads_before = device.reads

    async def failing_write(value):
        device.set_dimmer_value = FakeDevice.set_dimmer_value.__get__(device)
        raise RuntimeError("write failed")

    device.set_dimmer_value = failing_write
    controller._last_sent = 50  # force a write on the next tick, which then fails

    await wait_until(lambda: not controller.loop_ok)
    await wait_until(lambda: device.reads > reads_before, lambda: device.reads)


async def test_a_fault_outside_the_device_keeps_the_cached_value(fast_loop, started):
    """A GUI callback that raises must not cost a serial round trip on every tick of the outage."""
    device = FakeDevice(value=0)
    controller = await started(make_controller(device))
    await wait_until(lambda: 100 in device.writes, lambda: device.writes)
    reads_before = device.reads

    def failing_temps(*_args):
        raise RuntimeError("temperature callback failed")

    controller.set_callbacks(on_temps_change=failing_temps)
    await wait_until(lambda: not controller.loop_ok)
    controller.set_callbacks()
    await wait_until(lambda: controller.loop_ok)

    assert device.reads == reads_before


async def test_a_fault_in_the_loop_itself_is_logged_once_and_the_loop_lives(fast_loop, started, caplog, monkeypatch):
    """The wait between ticks sits outside the tick. Failing on every iteration must cost one log line."""
    sensors = ScriptedSensors([90])
    controller = make_controller(FakeDevice(), sensors)
    monkeypatch.setattr(CONFIG.control, "delay", "not a number")  # asyncio.sleep raises on every iteration

    with caplog.at_level(logging.ERROR):
        await started(controller)
        await wait_until(lambda: sensors.index >= 5, lambda: sensors.index)

    itself = [r for r in caplog.records if "control loop itself" in r.message]
    assert len(itself) == 1, f"one lasting fault over {sensors.index} ticks, {len(itself)} error lines"
    assert itself[0].exc_info, "the first report has to carry the traceback"
    assert not controller._loop_task.done()


async def test_the_end_of_a_fault_in_the_loop_itself_is_announced(fast_loop, started, caplog, monkeypatch):
    controller = make_controller(FakeDevice())
    monkeypatch.setattr(CONFIG.control, "delay", "not a number")

    with caplog.at_level(logging.INFO):
        await started(controller)
        await wait_until(lambda: any("control loop itself" in r.message for r in caplog.records))
        monkeypatch.setattr(CONFIG.control, "delay", 0.01)
        await wait_until(lambda: any("itself is working again" in r.message for r in caplog.records))


async def test_a_status_callback_that_raises_does_not_end_the_loop(fast_loop, started):
    """The status callback is GUI code; a raise in it costs a retry, not the loop."""

    def callback(*_args):
        raise RuntimeError("the GUI went away")

    controller = make_controller(FlakyDevice(failures=1))
    await started(controller)
    controller.set_callbacks(on_status_change=callback)

    await wait_until(lambda: 100 in controller.device.writes, lambda: controller.device.writes)

    assert not controller._loop_task.done()


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


async def test_a_fault_stays_reported_in_the_middle_of_the_next_tick(fast_loop, started):
    """Only a tick that finishes cleanly ends a fault; starting one does not."""
    sensors = SensorsThatPause()
    controller = await started(make_controller(FlakyDevice(failures=1000), sensors))
    try:
        await wait_until(sensors.paused.is_set)

        assert controller.device.failed == 1, "test setup: the first tick must have failed"
        assert not controller.loop_ok
    finally:
        sensors.release.set()
