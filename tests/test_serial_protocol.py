"""Reading a value back from the device, against a stub that behaves like the Tasmota console.

The device answers a set as well as a query. Those replies used to sit unread in the input buffer,
and the next query returned the oldest of them -- a value this app had written seconds earlier, which
the controller then reported as "changed outside this app" while being the only thing on the port.
"""

from collections import deque

import pytest

from iets_speed_control.entities.dimmer import Dimmer

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class FakeSerial:
    """Answers every command with a RESULT line, the way the Tasmota serial console does."""

    is_open = True
    closed = False
    in_waiting = 0

    def __init__(self, value: int = 0):
        self.value = value
        self.pending: deque[bytes] = deque()
        self.written: list[str] = []
        self.discards = 0

    async def write_async(self, data: bytes):
        command = data.decode().strip()
        self.written.append(command)
        field, _, argument = command.partition(" ")
        if argument:
            self.value = int(argument)

        self.pending.append(f'00:00:01.000 RSL: RESULT = {{"{field}":{self.value}}}\n'.encode())

    async def read_until_async(self, *_args, **_kwargs) -> bytes:
        return self.pending.popleft() if self.pending else b""

    def reset_input_buffer(self):
        self.discards += len(self.pending)
        self.pending.clear()

    def close(self):
        self.is_open = False


@pytest.fixture
def device(monkeypatch):
    """A Dimmer talking to the stub, with the inter-command wait removed to keep the test quick."""
    from iets_speed_control.entities import serial_device

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(serial_device.asyncio, "sleep", no_wait)

    dimmer = Dimmer(port="FAKE", dimmer_command="Dimmer")
    dimmer.serial = FakeSerial()
    return dimmer


# --- reading back what we wrote -------------------------------------------------------------------


async def test_a_read_after_one_write_returns_that_value(device):
    await device.set_dimmer_value(66)

    assert await device.read_dimmer_value() == 66


async def test_replies_left_by_earlier_writes_are_not_mistaken_for_the_answer(device):
    """Three writes in a row leave three unread replies; the query must still answer for itself."""
    for value in (72, 69, 66):
        await device.set_dimmer_value(value)

    assert await device.read_dimmer_value() == 66, "this is the value that looked like an outside change"


async def test_stale_replies_are_discarded_rather_than_accumulating(device):
    for value in (72, 69, 66):
        await device.set_dimmer_value(value)

    await device.read_dimmer_value()

    assert device.serial.discards == 3, "the replies to those writes have to be dropped, not queued"
    assert not device.serial.pending, "nothing may be left for the next query to pick up"


def answers_with(device, *lines: bytes):
    """Make the next command produce exactly these reply lines."""

    async def reply(_data):
        device.serial.pending.extend(lines)

    device.serial.write_async = reply


async def test_the_newest_reply_wins_when_a_query_answers_more_than_once(device):
    """Some firmware echoes a command and then answers it; the answer is the last line, not the first."""
    answers_with(
        device,
        b'00:00:01.000 RSL: RESULT = {"Dimmer":11}\n',
        b'00:00:01.000 RSL: RESULT = {"Dimmer":22}\n',
    )

    assert await device.read_dimmer_value() == 22


async def test_a_reply_for_another_field_is_ignored(device):
    answers_with(device, b'00:00:01.000 RSL: RESULT = {"POWER":"ON"}\n')

    assert await device.read_dimmer_value() is None


async def test_no_reply_at_all_reads_as_unknown(device):
    answers_with(device)

    assert await device.read_dimmer_value() is None


async def test_a_query_with_no_answer_does_not_fall_back_to_a_stale_reply(device):
    """This is what discarding the buffer buys: taking the newest reply is not enough on its own.

    When the device does not answer, the newest thing in the buffer is a reply to an earlier write,
    and reporting that as the current value is how the false "changed outside this app" appeared.
    """
    await device.set_dimmer_value(66)
    answers_with(device)  # the device stays silent this time

    assert await device.read_dimmer_value() is None


async def test_noise_on_the_line_does_not_become_a_value(device):
    answers_with(device, b"boot: rst cause 1, boot mode 3\n", b"00:00:01.000 RSL: RESULT = {broken\n")

    assert await device.read_dimmer_value() is None


# --- the controller stops crying wolf -------------------------------------------------------------


async def test_the_controller_does_not_report_an_outside_change_after_its_own_writes(device, caplog, monkeypatch):
    """The exact symptom from the log: one instance, no other writer, yet a warning every few seconds."""
    import logging

    from iets_speed_control.controller import SpeedController
    from iets_speed_control.util.config import CONFIG

    monkeypatch.setattr(CONFIG.control, "resync_every", 30)
    controller = SpeedController(sensor_provider=_FixedSensors())
    controller.device = device

    # The real pattern: many writes, and a read-back only every resync_every ticks. That is what let
    # the unread replies pile up.
    with caplog.at_level(logging.WARNING):
        for value in (72, 69, 66):
            await controller._set_fan_speed(value)

        controller._ticks_since_resync = 99  # the resync tick has come round
        reported = await controller._current_dimmer()

    assert reported == 66, f"the device holds 66; the read-back said {reported}"
    assert "changed outside this app" not in caplog.text, caplog.text


class _FixedSensors:
    name = "fixed"

    def get_temperatures(self):
        return {"CPU": 70.0, "GPU": 70.0}
