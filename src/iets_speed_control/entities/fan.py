"""What the controller needs from a fan, whatever drives it.

The controller only sets a speed, reads it back, and opens or closes the link. How the speed reaches
the fan (a Tasmota console over a serial port today) is the device's business, so another kind of
device is one new class with these methods and no change to the control loop.
"""

from typing import Protocol


class FanDevice(Protocol):
    """A fan whose speed is set and read as a percentage, 0-100."""

    port: str | None
    """Where the device is reached, as shown to the user, for example "COM7"."""

    @property
    def connected(self) -> bool:
        """Whether the device can be talked to right now."""
        ...

    def describe(self) -> str:
        """One line for the log about how the device is reached."""
        ...

    async def connect(self) -> bool:
        """Open the link, finding the device first if needed. Returns whether it is connected."""
        ...

    async def disconnect(self) -> None: ...

    async def read_speed(self) -> int | None:
        """The speed the device reports, or None when it does not answer."""
        ...

    async def set_speed(self, value: int) -> None: ...
