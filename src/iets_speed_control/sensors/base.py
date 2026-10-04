"""Sensor provider contract shared by all temperature sources."""

import ctypes
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


def is_elevated() -> bool:
    """Whether this process runs with administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except AttributeError, OSError:
        return False


def lacks_admin_rights(provider: SensorProvider | type[SensorProvider] | None) -> bool:
    """Whether `provider` (an instance or a class) cannot work because this process is not elevated.

    A source that needs administrator rights reports itself by `requires_admin`; the others never do.
    """
    return bool(getattr(provider, "requires_admin", False)) and not is_elevated()


def to_temperature(value) -> float | None:
    """Coerce a raw WMI sensor value to a float, or None when it is not numeric."""
    try:
        return float(value)
    except TypeError, ValueError:
        return None
