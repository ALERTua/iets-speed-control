"""A thin ctypes layer over the NVIDIA driver's own library, nvapi64.dll.

The DLL ships with every NVIDIA display driver and answers an ordinary process, so nothing here needs
administrator rights. Its only export is `nvapi_QueryInterface`, which turns a numeric interface id into
a function address. The ids and the structure layouts below are the ones LibreHardwareMonitor uses
(LibreHardwareMonitorLib/Interop/NvApi.cs); NVIDIA documents only some of them.

The class does no interpretation: a failed call raises `NvapiError`, a successful one returns plain
Python values. The temperature source above it can therefore be tested against a stand-in driver.
"""

import ctypes
from collections.abc import Callable
from typing import Protocol

# NvAPI_Status: zero is NVAPI_OK, every error is negative.
STATUS_OK = 0
# NVAPI_GPU_NOT_POWERED: the GPU is switched off, as an Optimus laptop's NVIDIA GPU is when nothing uses it.
STATUS_GPU_NOT_POWERED = -220

MAX_PHYSICAL_GPUS = 64
SHORT_STRING_MAX = 64
THERMAL_SENSOR_RESERVED_COUNT = 8
THERMAL_SENSOR_TEMPERATURE_COUNT = 32
MAX_THERMAL_SENSORS_PER_GPU = 3

# NV_THERMAL_TARGET values NvAPI_GPU_GetThermalSettings takes and reports.
THERMAL_TARGET_GPU = 1
THERMAL_TARGET_ALL = 15

INTERFACE_INITIALIZE = 0x0150E828
INTERFACE_ENUM_PHYSICAL_GPUS = 0xE5AC921F
INTERFACE_GET_FULL_NAME = 0xCEEE8E9F
INTERFACE_GET_THERMAL_SETTINGS = 0xE3640A56
INTERFACE_GET_THERMAL_SENSORS = 0x65FE3AAD
INTERFACE_GET_CURRENT_PSTATE = 0x927DA4F6


class NvapiError(OSError):
    """An NVAPI call answered with an error status, or the driver lacks the interface.

    `status` is the NVAPI status, or None when the call never happened.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NvapiDriver(Protocol):
    """What the temperature source needs from the driver. NvapiLibrary is the real one."""

    def initialize(self) -> None: ...

    def physical_gpus(self) -> list[int]:
        """The handles of the NVIDIA GPUs, in the driver's order."""
        ...

    def gpu_name(self, gpu: int) -> str: ...

    def current_pstate(self, gpu: int) -> int:
        """The GPU's performance state. Raises NvapiError with STATUS_GPU_NOT_POWERED while it is switched off.

        Other monitors (Lenovo Legion Toolkit, G-Helper) use this as the check before any other read, on the
        understanding that it does not power the GPU on, which the thermal calls do.
        """
        ...

    def core_temperature(self, gpu: int) -> float | None:
        """The documented core sensor in °C, or None when the driver reports none right now."""
        ...

    def thermal_sensors(self, gpu: int, mask: int) -> list[int]:
        """The undocumented sensor table as the driver gives it, in 1/256 °C: 32 entries, 0 where a slot is empty.

        Raises NvapiError when the driver refuses `mask`, which is how the usable mask is found.
        """
        ...


class _ThermalSensor(ctypes.Structure):
    _fields_ = [
        ("controller", ctypes.c_int),
        ("default_min_temp", ctypes.c_uint),
        ("default_max_temp", ctypes.c_uint),
        ("current_temp", ctypes.c_uint),
        ("target", ctypes.c_int),
    ]


class _ThermalSettings(ctypes.Structure):
    """NV_GPU_THERMAL_SETTINGS version 2."""

    _fields_ = [
        ("version", ctypes.c_uint),
        ("count", ctypes.c_uint),
        ("sensor", _ThermalSensor * MAX_THERMAL_SENSORS_PER_GPU),
    ]


class _ThermalSensors(ctypes.Structure):
    """The undocumented structure of NvAPI_GPU_GetThermalSensors, version 2."""

    _fields_ = [
        ("version", ctypes.c_uint),
        ("mask", ctypes.c_uint),
        ("reserved", ctypes.c_int * THERMAL_SENSOR_RESERVED_COUNT),
        ("temperatures", ctypes.c_int * THERMAL_SENSOR_TEMPERATURE_COUNT),
    ]


def _version(structure: type[ctypes.Structure], version: int) -> int:
    """NVAPI's MAKE_NVAPI_VERSION: the structure size in the low word, its version in the high one."""
    return ctypes.sizeof(structure) | (version << 16)


class NvapiLibrary:
    """The real driver. Constructing it loads the DLL, so it raises OSError on a machine without NVIDIA's."""

    def __init__(self):
        library = ctypes.CDLL("nvapi64.dll")
        self._query_interface = library.nvapi_QueryInterface
        self._query_interface.restype = ctypes.c_void_p
        self._query_interface.argtypes = [ctypes.c_uint]
        self._functions: dict[int, Callable[..., int]] = {}

    def _function(self, interface_id: int, *argtypes) -> Callable[..., int]:
        function = self._functions.get(interface_id)
        if function is None:
            address = self._query_interface(interface_id)
            if not address:
                raise NvapiError(f"NVAPI has no interface 0x{interface_id:08X}")

            function = ctypes.CFUNCTYPE(ctypes.c_int, *argtypes)(address)
            self._functions[interface_id] = function

        return function

    def _call(self, what: str, interface_id: int, argtypes: tuple, *args) -> None:
        status = self._function(interface_id, *argtypes)(*args)
        if status != STATUS_OK:
            raise NvapiError(f"{what} failed with NVAPI status {status}", status)

    def initialize(self) -> None:
        self._call("NvAPI_Initialize", INTERFACE_INITIALIZE, ())

    def physical_gpus(self) -> list[int]:
        handles = (ctypes.c_void_p * MAX_PHYSICAL_GPUS)()
        count = ctypes.c_int()
        argtypes = (ctypes.c_void_p * MAX_PHYSICAL_GPUS, ctypes.POINTER(ctypes.c_int))
        self._call("NvAPI_EnumPhysicalGPUs", INTERFACE_ENUM_PHYSICAL_GPUS, argtypes, handles, ctypes.byref(count))
        return [handle for handle in handles[: count.value] if handle]

    def gpu_name(self, gpu: int) -> str:
        name = ctypes.create_string_buffer(SHORT_STRING_MAX)
        argtypes = (ctypes.c_void_p, ctypes.c_char * SHORT_STRING_MAX)
        self._call("NvAPI_GPU_GetFullName", INTERFACE_GET_FULL_NAME, argtypes, gpu, name)
        return name.value.decode("ascii", errors="replace")

    def current_pstate(self, gpu: int) -> int:
        pstate = ctypes.c_int()
        argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
        self._call("NvAPI_GPU_GetCurrentPstate", INTERFACE_GET_CURRENT_PSTATE, argtypes, gpu, ctypes.byref(pstate))
        return pstate.value

    def core_temperature(self, gpu: int) -> float | None:
        settings = _ThermalSettings(version=_version(_ThermalSettings, 2), count=MAX_THERMAL_SENSORS_PER_GPU)
        argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_ThermalSettings))
        self._call(
            "NvAPI_GPU_GetThermalSettings",
            INTERFACE_GET_THERMAL_SETTINGS,
            argtypes,
            gpu,
            THERMAL_TARGET_ALL,
            ctypes.byref(settings),
        )
        # The driver answers OK with a count of 0 when it is polled too fast, so that is "no value now".
        for sensor in settings.sensor[: settings.count]:
            if sensor.target == THERMAL_TARGET_GPU:
                return float(sensor.current_temp)

        return None

    def thermal_sensors(self, gpu: int, mask: int) -> list[int]:
        sensors = _ThermalSensors(version=_version(_ThermalSensors, 2), mask=mask)
        argtypes = (ctypes.c_void_p, ctypes.POINTER(_ThermalSensors))
        self._call("NvAPI_GPU_GetThermalSensors", INTERFACE_GET_THERMAL_SENSORS, argtypes, gpu, ctypes.byref(sensors))
        return list(sensors.temperatures)
