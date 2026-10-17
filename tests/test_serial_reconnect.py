"""The serial link comes back on its own after the port dies under it, as it does across a hibernation.

After a resume the USB serial driver can leave the old handle half alive: writes fail with "Incorrect
function", or a request never completes at all, while the handle still holds the port so that opening it
again fails with "Access is denied". The device closes a handle that failed and opens a fresh one on the
next tick, bounds every exchange in time, and the controller reopens the port after the machine slept.
"""

import asyncio
import logging

import pytest
from conftest import controller_tracking
from serial.serialutil import SerialException
from test_control_smoothing import FakeDevice, ScriptedSensors

from iets_speed_control import controller as controller_module
from iets_speed_control.entities import serial_device
from iets_speed_control.entities.tasmota_fan import TasmotaSerialFan

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class DyingSerial:
    """A port whose writes and reads fail, or hang, the way a handle the driver gave up on does."""

    closed = False

    def __init__(self):
        self.write_error = None
        self.hang_writes = False
        self.hang_reads = False
        self.is_open = True
        self.invalidated = False
        self.close_calls = 0

    @property
    def in_waiting(self):
        if self.invalidated or not self.is_open:
            raise SerialException("ClearCommError failed (OSError(9, 'The handle is invalid.'))")
        return 0

    async def write_async(self, _data):
        if self.hang_writes:
            await asyncio.Event().wait()  # a request the driver never completes
        if self.write_error:
            raise self.write_error

    async def read_until_async(self, *_args, **_kwargs):
        if self.hang_reads:
            await asyncio.Event().wait()
        return b""

    def reset_input_buffer(self):
        pass

    def close(self):
        self.close_calls += 1
        self.is_open = False


@pytest.fixture
def opened(monkeypatch):
    """Every port the device opens, in order; opening a port while an earlier handle holds it is refused."""
    handles: list[DyingSerial] = []

    def open_port(**_kwargs):
        if any(handle.is_open for handle in handles):
            raise SerialException("could not open port 'COM7': PermissionError(13, 'Access is denied.', None, 5)")
        handle = DyingSerial()
        handles.append(handle)
        return handle

    monkeypatch.setattr(serial_device.aioserial, "AioSerial", open_port)
    monkeypatch.setattr(serial_device, "EXCHANGE_TIMEOUT", 0.05)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(serial_device.asyncio, "sleep", no_wait)
    return handles


async def test_a_failed_write_closes_the_handle_so_the_port_can_open_again(opened):
    fan = TasmotaSerialFan(port="COM7")
    assert await fan.connect()
    opened[0].write_error = SerialException("WriteFile failed (OSError(22, 'Incorrect function.', None, 1))")

    await fan.set_speed(50)

    assert opened[0].close_calls == 1, "the dead handle has to be closed, or it keeps holding the port"
    assert not fan.connected
    assert await fan.connect(), "a fresh handle must open once the dead one is closed"
    assert len(opened) == 2


async def test_a_write_that_never_finishes_is_cut_off(opened):
    fan = TasmotaSerialFan(port="COM7")
    assert await fan.connect()
    opened[0].hang_writes = True

    async with asyncio.timeout(2):
        await fan.set_speed(50)

    assert opened[0].close_calls == 1
    assert not fan.connected


async def test_a_read_that_never_finishes_is_cut_off(opened):
    fan = TasmotaSerialFan(port="COM7")
    assert await fan.connect()
    opened[0].hang_reads = True

    async with asyncio.timeout(2):
        assert await fan.read_speed() is None

    assert opened[0].close_calls == 1
    assert not fan.connected


async def test_a_handle_that_stopped_answering_is_closed_before_the_port_is_opened_again(opened):
    fan = TasmotaSerialFan(port="COM7")
    assert await fan.connect()
    first = opened[0]
    first.invalidated = True  # the driver gave up on the handle without any exchange failing first

    assert not fan.connected
    assert await fan.connect()
    assert first.close_calls == 1
    assert len(opened) == 2


async def test_a_deliberate_disconnect_is_not_reported_as_a_lost_port(opened, caplog):
    """The controller closes the port itself after the machine slept; reopening it is no fault."""
    fan = TasmotaSerialFan(port="COM7")
    assert await fan.connect()

    with caplog.at_level(logging.WARNING):
        await fan.disconnect()
        assert await fan.connect()

    assert opened[0].close_calls == 1
    assert "Lost" not in caplog.text, caplog.text


async def test_an_outage_is_logged_once_and_its_end_too(monkeypatch, caplog):
    attempts = {"count": 0}

    def refuse(**_kwargs):
        attempts["count"] += 1
        if attempts["count"] <= 3:
            raise SerialException("could not open port 'COM7': FileNotFoundError(2, 'The system cannot find the file')")
        return DyingSerial()

    monkeypatch.setattr(serial_device.aioserial, "AioSerial", refuse)
    fan = serial_device.SerialDevice(port="COM7")

    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            await fan.connect()

    messages = [record.getMessage() for record in caplog.records]
    assert sum("Unable to connect" in message for message in messages) == 1, messages
    assert any(message == "Connected to COM7" for message in messages), "the recovery has to reach a WARNING log"


# --- the controller after the machine slept --------------------------------------------------------


class CountingDevice(FakeDevice):
    def __init__(self):
        super().__init__()
        self.disconnects = 0
        self.connects = 0

    async def connect(self):
        self.connects += 1
        self.connected = True
        return True

    async def disconnect(self):
        self.disconnects += 1
        self.connected = False


@pytest.fixture
def clock(monkeypatch):
    now = {"value": 1000.0}
    monkeypatch.setattr(controller_module.time, "time", lambda: now["value"])
    return now


async def test_a_long_gap_between_ticks_reopens_the_port(clock):
    controller = controller_tracking(ScriptedSensors([60]))
    controller.device = device = CountingDevice()

    await controller._tick()
    clock["value"] += 3600  # an hour in hibernation
    await controller._tick()

    assert device.disconnects == 1, "a handle that came out of hibernation is not to be trusted"
    assert device.connects == 1 and device.connected


async def test_ordinary_ticks_leave_the_port_alone(clock):
    controller = controller_tracking(ScriptedSensors([60]))
    controller.device = device = CountingDevice()

    for _ in range(5):
        await controller._tick()
        clock["value"] += 1.5

    assert device.disconnects == 0


async def test_a_reconnected_device_is_read_again_rather_than_trusted_from_memory(clock):
    """The last value written may never have reached a device that dropped off."""
    controller = controller_tracking(ScriptedSensors([60]))
    controller.device = device = CountingDevice()
    await controller._tick()
    reads = device.reads

    device.connected = False
    clock["value"] += 1.5
    await controller._tick()

    assert device.reads == reads + 1
