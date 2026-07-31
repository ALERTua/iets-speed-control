"""Rolling-median smoothing and the dimmer read-back policy.

Uses a fake device so the real fan is never driven, and a fake sensor source so temperatures are
scripted rather than whatever the machine happens to be doing.
"""

import asyncio

import pytest

from iets_speed_control.controller import Mode, SpeedController
from iets_speed_control.util import env
from iets_speed_control.util.tools import MedianSmoother


class FakeDevice:
    """Records every serial interaction; never touches a port."""

    port = "FAKE"
    connected = True

    def __init__(self, value: int = 0):
        self.value = value
        self.reads = 0
        self.writes: list[int] = []

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def read_dimmer_value(self):
        self.reads += 1
        return self.value

    async def set_dimmer_value(self, value):
        self.value = value
        self.writes.append(value)


class ScriptedSensors:
    """Replays a list of CPU temperatures, holding the last one once exhausted."""

    name = "scripted"

    def __init__(self, temperatures):
        self.temperatures = list(temperatures)
        self.index = 0

    def get_temperatures(self):
        value = self.temperatures[min(self.index, len(self.temperatures) - 1)]
        self.index += 1
        return {"CPU Package": float(value), "GPU Core": 0.0}


@pytest.fixture
def controller_env(monkeypatch):
    """Deterministic, fast control-loop settings."""
    monkeypatch.setattr(env, "DELAY", 0.01)
    monkeypatch.setattr(env, "CPU_SENSOR_FILTER", "CPU")
    monkeypatch.setattr(env, "GPU_SENSOR_FILTER", "GPU")
    monkeypatch.setattr(env, "MAX_STEP", 100)
    monkeypatch.setattr(env, "IGNORE_LESS_THAN", 0)
    # Two ranges, not one: a single range would eval() to a flat tuple of ints. Linear 0..100 C -> 0..50.
    monkeypatch.setattr(env, "TEMP_RANGES", "(0, 100, 0, 50), (100, 200, 50, 100)")


async def run_loop(controller, ticks: int, delay: float = 0.01):
    await controller.start()
    await asyncio.sleep(delay * ticks)
    await controller.stop()


# --- MedianSmoother ---------------------------------------------------------------------------


def test_smoother_window_of_one_passes_values_through():
    smoother = MedianSmoother(1)

    assert [smoother.add(v) for v in (40, 90, 41)] == [40, 90, 41]


def test_smoother_absorbs_a_single_spike():
    smoother = MedianSmoother(5)
    for value in (60, 60, 60, 60):
        smoother.add(value)

    assert smoother.add(95) == 60, "one spurious reading must not move the median"


def test_smoother_follows_a_sustained_change():
    smoother = MedianSmoother(5)
    for value in (60, 60, 60, 60, 60):
        smoother.add(value)

    results = [smoother.add(90) for _ in range(3)]

    assert results[-1] == 90, f"a sustained rise must be tracked, got {results}"


@pytest.mark.parametrize("window", [0, -3])
def test_smoother_rejects_a_useless_window(window):
    assert MedianSmoother(window).window == 1


def test_smoother_reset_drops_history():
    smoother = MedianSmoother(3)
    smoother.add(10)
    smoother.reset()

    assert smoother.samples == ()
    assert smoother.add(80) == 80


# --- read-back policy -------------------------------------------------------------------------


async def test_dimmer_is_not_re_read_every_tick(controller_env, monkeypatch):
    """The cached last-written value replaces a serial round-trip on every tick."""
    monkeypatch.setattr(env, "RESYNC_EVERY", 0)
    monkeypatch.setattr(env, "TEMP_WINDOW", 1)

    controller = SpeedController(sensor_provider=ScriptedSensors([70]))
    device = FakeDevice(0)
    controller.device = device

    await run_loop(controller, ticks=12)

    assert device.reads == 1, f"expected a single initial read, got {device.reads}"


async def test_dimmer_is_re_read_on_the_resync_tick(controller_env, monkeypatch):
    monkeypatch.setattr(env, "RESYNC_EVERY", 3)
    monkeypatch.setattr(env, "TEMP_WINDOW", 1)

    controller = SpeedController(sensor_provider=ScriptedSensors([70]))
    device = FakeDevice(0)
    controller.device = device

    await run_loop(controller, ticks=14)

    assert device.reads > 1, "the device must be polled again on the resync tick"


async def test_external_change_is_picked_up_and_reported(controller_env, monkeypatch, caplog):
    """Someone moving the dimmer via the Tasmota UI must not be silently overwritten."""
    monkeypatch.setattr(env, "RESYNC_EVERY", 2)
    monkeypatch.setattr(env, "TEMP_WINDOW", 1)
    monkeypatch.setattr(env, "MAX_STEP", 0)

    controller = SpeedController(sensor_provider=ScriptedSensors([70]))
    device = FakeDevice(0)
    controller.device = device
    controller.mode = Mode.MANUAL
    controller.manual_speed = 50

    await controller.start()
    await asyncio.sleep(0.05)
    device.value = 17  # as if changed from outside
    await asyncio.sleep(0.1)
    await controller.stop()

    assert "changed outside this app" in caplog.text


async def test_reconnect_forces_a_fresh_read(controller_env, monkeypatch):
    monkeypatch.setattr(env, "RESYNC_EVERY", 0)
    monkeypatch.setattr(env, "TEMP_WINDOW", 1)

    controller = SpeedController(sensor_provider=ScriptedSensors([70]))
    device = FakeDevice(0)
    controller.device = device

    await controller.start()
    await asyncio.sleep(0.05)
    reads_before = device.reads

    device.connected = False  # link drops
    await asyncio.sleep(0.05)
    device.connected = True  # and comes back
    await asyncio.sleep(0.05)
    await controller.stop()

    assert device.reads > reads_before, "a reconnect must not trust the cached value"


# --- smoothing inside the loop ----------------------------------------------------------------


async def test_a_single_spike_does_not_move_the_fan(controller_env, monkeypatch):
    """60 C steady with one 95 C blip: the median swallows it, so no new value is written."""
    monkeypatch.setattr(env, "RESYNC_EVERY", 0)
    monkeypatch.setattr(env, "TEMP_WINDOW", 5)

    controller = SpeedController(sensor_provider=ScriptedSensors([60, 60, 60, 60, 60, 95, 60, 60, 60]))
    device = FakeDevice(30)
    controller.device = device

    await run_loop(controller, ticks=12)

    assert 47 not in device.writes, f"the 95 C spike leaked into the fan speed: {device.writes}"


async def test_fractional_readings_are_rounded_not_truncated(controller_env, monkeypatch):
    """LibreHardwareMonitor reports values like 60.6; int() would report 60."""
    monkeypatch.setattr(env, "RESYNC_EVERY", 0)
    monkeypatch.setattr(env, "TEMP_WINDOW", 1)

    controller = SpeedController(sensor_provider=ScriptedSensors([60.6]))
    controller.device = FakeDevice(0)

    await run_loop(controller, ticks=3)

    assert controller.cpu_temp == 61
