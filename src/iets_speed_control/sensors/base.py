"""Sensor provider contract shared by all temperature sources."""

import threading
from typing import Protocol

import pythoncom

_com_state = threading.local()


class SensorProvider(Protocol):
    """A source of temperature readings.

    Implementations are blocking by contract: callers run them off the event loop.
    """

    name: str

    def get_temperatures(self) -> dict[str, float]:
        """Return {sensor label: temperature}. Returns an empty dict when the source is unavailable."""
        ...


def ensure_com_initialized():
    """CoInitialize the calling thread once.

    Deliberately never paired with CoUninitialize: WMI runs on asyncio thread-pool threads that
    live for the whole process, and tearing COM down while wmi/pywin32 objects are still reachable
    (exception tracebacks keep them alive) prints "Win32 exception occurred releasing IUnknown".
    """
    if getattr(_com_state, "initialized", False):
        return

    # noinspection PyUnresolvedReferences
    pythoncom.CoInitialize()  # type: ignore[union-attr]
    _com_state.initialized = True


def to_temperature(value) -> float | None:
    """Coerce a raw WMI sensor value to a float, or None when it is not numeric."""
    try:
        return float(value)
    except TypeError, ValueError:
        return None
