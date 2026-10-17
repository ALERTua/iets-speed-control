"""The NVIDIA source, against a stand-in driver and, for the ctypes layer, against stand-in DLL functions.

The e2e tests read the real driver and skip without one. These pin what needs no GPU: which table slot
is which label for each GPU series, the 1/256 scaling, how the mask is found, and that nothing a driver
does can make a poll raise.
"""

import ctypes
import logging
import threading
import time

import pytest

from iets_speed_control.sensors import (
    PROVIDERS,
    NvidiaProvider,
    create_provider,
    find_provider,
    lacks_admin_rights,
    nvapi,
    nvidia,
)
from iets_speed_control.sensors import base as sensors_base
from iets_speed_control.sensors.base import is_plausible

RTX_4090 = "NVIDIA GeForce RTX 4090 Laptop GPU"
RTX_3080 = "NVIDIA GeForce RTX 3080 Laptop GPU"
RTX_5090 = "NVIDIA GeForce RTX 5090"


def raw(celsius: float) -> int:
    """A temperature as the undocumented table holds it: 1/256 °C."""
    return round(celsius * 256)


def table_with(slots: dict[int, float]) -> list[int]:
    table = [0] * nvapi.THERMAL_SENSOR_TEMPERATURE_COUNT
    for slot, celsius in slots.items():
        table[slot] = raw(celsius)
    return table


class FakeDriver:
    """NvapiDriver with scripted answers; it refuses a mask that names a sensor beyond `sensor_count`."""

    def __init__(self, name=RTX_4090, table=None, sensor_count=8, core=54.0, gpus=(0x1000,)):
        self.name = name
        self.table = table if table is not None else table_with({1: 70.5, 7: 80.0})
        self.sensor_count = sensor_count
        self.core = core
        self.gpus = list(gpus)
        self.initialize_error: Exception | None = None
        self.core_error: Exception | None = None
        self.table_error: Exception | None = None
        self.pstate_error: Exception | None = None
        self.initialized = 0
        self.pstate_calls = 0
        self.name_calls = 0
        self.table_masks: list[int] = []
        self.core_calls = 0

    def initialize(self):
        self.initialized += 1
        if self.initialize_error:
            raise self.initialize_error

    def physical_gpus(self):
        return list(self.gpus)

    def gpu_name(self, gpu):
        self.name_calls += 1
        return self.name

    def current_pstate(self, gpu):
        self.pstate_calls += 1
        if self.pstate_error:
            raise self.pstate_error
        return 0

    def fall_asleep(self):
        self.pstate_error = nvapi.NvapiError("not powered", nvapi.STATUS_GPU_NOT_POWERED)

    def wake_up(self):
        self.pstate_error = None

    def core_temperature(self, gpu):
        self.core_calls += 1
        if self.core_error:
            raise self.core_error
        return self.core

    def thermal_sensors(self, gpu, mask):
        self.table_masks.append(mask)
        if mask >> self.sensor_count:
            raise nvapi.NvapiError("NvAPI_GPU_GetThermalSensors failed with NVAPI status -5")
        if self.table_error:
            raise self.table_error
        return list(self.table)


def provider_for(driver: FakeDriver, clock=time.monotonic) -> NvidiaProvider:
    return NvidiaProvider(driver_factory=lambda: driver, clock=clock)


def test_an_rtx_40_reads_its_core_hot_spot_and_memory_junction():
    readings = provider_for(FakeDriver()).get_temperatures()

    assert readings == {"GPU Core": 54.0, "GPU Hot Spot": 70.5, "GPU Memory Junction": 80.0}


def test_the_table_is_in_256ths_of_a_degree():
    driver = FakeDriver(table=table_with({1: 70.0 + 1 / 256, 7: 0.5}))

    readings = provider_for(driver).get_temperatures()

    assert readings["GPU Hot Spot"] == pytest.approx(70.00390625)
    assert readings["GPU Memory Junction"] == 0.5


def test_any_other_series_has_its_memory_junction_in_slot_9():
    driver = FakeDriver(name=RTX_3080, table=table_with({1: 66.0, 7: 11.0, 9: 77.0}), sensor_count=12)

    readings = provider_for(driver).get_temperatures()

    assert readings == {"GPU Core": 54.0, "GPU Hot Spot": 66.0, "GPU Memory Junction": 77.0}


def test_an_rtx_50_takes_its_core_from_the_table_and_has_no_hot_spot():
    driver = FakeDriver(name=RTX_5090, table=table_with({1: 61.0, 2: 74.0, 7: 99.0}), core=12.0)

    readings = provider_for(driver).get_temperatures()

    assert readings == {"GPU Core": 61.0, "GPU Memory Junction": 74.0}
    assert driver.core_calls == 0, "on this series the documented sensor is not the core"


def test_the_series_is_matched_by_the_start_of_the_name_and_ignores_case():
    assert nvidia.sensor_slots("nvidia geforce rtx 5070 ti") == {"GPU Core": 1, "GPU Memory Junction": 2}
    assert nvidia.sensor_slots("NVIDIA GeForce RTX 4060 Laptop GPU") == {"GPU Hot Spot": 1, "GPU Memory Junction": 7}
    # A card named like a 40 series but not starting with it is not one.
    assert nvidia.sensor_slots("NVIDIA RTX A4000") == {"GPU Hot Spot": 1, "GPU Memory Junction": 9}


def test_a_zero_slot_is_a_missing_sensor_and_is_dropped():
    """A 0 °C entry would match the filters and read as a cold GPU instead of a missing sensor."""
    driver = FakeDriver(table=table_with({1: 0.0, 7: 80.0}))

    assert provider_for(driver).get_temperatures() == {"GPU Core": 54.0, "GPU Memory Junction": 80.0}


@pytest.mark.parametrize("core", [0.0, None])
def test_a_core_the_driver_does_not_report_is_left_out(core):
    assert "GPU Core" not in provider_for(FakeDriver(core=core)).get_temperatures()


def test_the_mask_grows_until_the_first_refusal():
    driver = FakeDriver(sensor_count=8)

    provider_for(driver).get_temperatures()

    assert driver.table_masks[:9] == [1 << bit for bit in range(9)], "bit 8 is the first the driver refuses"
    assert driver.table_masks[-1] == 0xFF, "the reading itself asks for every sensor at once"


def test_a_gpu_that_accepts_every_bit_gets_the_full_mask():
    assert nvidia.find_mask(FakeDriver(sensor_count=32), 0x1000) == 0xFFFFFFFF


def test_a_gpu_without_the_table_still_reports_its_core():
    driver = FakeDriver(sensor_count=0)

    assert provider_for(driver).get_temperatures() == {"GPU Core": 54.0}
    assert driver.table_masks == [1], "with no mask there is nothing to read after the search"


def test_the_setup_runs_once_not_on_every_poll():
    driver = FakeDriver()
    created = []
    provider = NvidiaProvider(driver_factory=lambda: created.append(1) or driver)

    for _ in range(5):
        provider.get_temperatures()

    assert len(created) == 1
    assert driver.initialized == 1
    assert driver.table_masks.count(1) == 1, "the mask search is not repeated"


def test_the_first_gpu_is_the_one_read():
    driver = FakeDriver(gpus=(0x1000, 0x2000))
    seen = []
    original = driver.core_temperature
    driver.core_temperature = lambda gpu: seen.append(gpu) or original(gpu)

    provider_for(driver).get_temperatures()

    assert seen == [0x1000]


def test_a_missing_library_reads_as_unavailable_and_says_so_once(caplog):
    calls = []

    def missing():
        calls.append(1)
        raise FileNotFoundError("nvapi64.dll")

    provider = NvidiaProvider(driver_factory=missing, clock=lambda: 0.0)

    with caplog.at_level(logging.DEBUG, logger=nvidia.__name__):
        for _ in range(3):
            assert provider.get_temperatures() == {}

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "nvapi64.dll" in errors[0].message
    assert len(calls) == 1, "a poll a second must not reload a DLL that is not there"


def test_a_failing_initialize_reads_as_unavailable(caplog):
    driver = FakeDriver()
    driver.initialize_error = nvapi.NvapiError("NvAPI_Initialize failed with NVAPI status -3")

    with caplog.at_level(logging.ERROR, logger=nvidia.__name__):
        assert provider_for(driver).get_temperatures() == {}

    assert any("NvAPI_Initialize" in record.message for record in caplog.records)
    assert driver.table_masks == [], "nothing is asked of a driver that did not initialize"


def test_no_gpu_reads_as_unavailable(caplog):
    with caplog.at_level(logging.ERROR, logger=nvidia.__name__):
        assert provider_for(FakeDriver(gpus=())).get_temperatures() == {}

    assert any("no NVIDIA GPU" in record.message for record in caplog.records)


def test_a_failed_setup_is_tried_again_after_a_minute(caplog):
    driver = FakeDriver()
    driver.initialize_error = nvapi.NvapiError("not ready")
    now = [100.0]
    provider = provider_for(driver, clock=lambda: now[0])
    assert provider.get_temperatures() == {}

    driver.initialize_error = None
    now[0] += NvidiaProvider.SETUP_RETRY_SECONDS - 1
    assert provider.get_temperatures() == {}, "too early to try again"
    assert driver.initialized == 1

    now[0] += 1
    assert provider.get_temperatures()["GPU Hot Spot"] == 70.5


def test_the_retry_does_not_repeat_the_error(caplog):
    driver = FakeDriver()
    driver.initialize_error = nvapi.NvapiError("not ready")
    now = [0.0]
    provider = provider_for(driver, clock=lambda: now[0])

    with caplog.at_level(logging.DEBUG, logger=nvidia.__name__):
        for _ in range(4):
            provider.get_temperatures()
            now[0] += NvidiaProvider.SETUP_RETRY_SECONDS

    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert driver.initialized == 4


def test_a_sleeping_gpu_reads_as_empty_without_touching_the_thermal_calls():
    driver = FakeDriver()
    driver.fall_asleep()
    provider = provider_for(driver)

    assert provider.get_temperatures() == {}

    assert provider.asleep
    assert driver.core_calls == 0
    assert driver.table_masks == []


def test_every_poll_of_a_sleeping_gpu_asks_only_for_its_state():
    """The state call is what is safe to repeat: the thermal calls would keep the GPU powered."""
    driver = FakeDriver()
    driver.fall_asleep()
    provider = provider_for(driver)

    for _ in range(3):
        provider.get_temperatures()

    assert driver.pstate_calls == 3
    assert driver.core_calls == 0
    assert driver.table_masks == []


def test_the_setup_that_needs_the_gpu_waits_until_it_is_awake():
    driver = FakeDriver()
    driver.fall_asleep()
    provider = provider_for(driver)
    provider.get_temperatures()

    assert driver.name_calls == 0, "GetFullName would wake the GPU"
    assert driver.table_masks == [], "so would the search for the mask"

    driver.wake_up()
    readings = provider.get_temperatures()

    assert readings == {"GPU Core": 54.0, "GPU Hot Spot": 70.5, "GPU Memory Junction": 80.0}
    assert not provider.asleep
    assert driver.name_calls == 1


def test_a_gpu_that_falls_asleep_while_its_mask_is_found_is_identified_again_later(caplog):
    """Settling on the short mask it had reached would lose the hot spot and the junction until a restart."""
    driver = FakeDriver()
    refuse_once = {"bit": 1 << 3}
    real_table = driver.thermal_sensors

    def table(gpu, mask):
        if mask == refuse_once.get("bit"):
            del refuse_once["bit"]
            driver.table_masks.append(mask)
            raise nvapi.NvapiError("not powered", nvapi.STATUS_GPU_NOT_POWERED)
        return real_table(gpu, mask)

    driver.thermal_sensors = table
    provider = provider_for(driver)

    with caplog.at_level(logging.DEBUG):
        assert provider.get_temperatures() == {}, "nothing is read from a GPU that went to sleep"
    assert provider.asleep
    assert "Cannot read" not in caplog.text, "a sleep is not a driver failure"
    calls_while_asleep = driver.core_calls

    readings = provider.get_temperatures()

    assert calls_while_asleep == 0
    assert readings == {"GPU Core": 54.0, "GPU Hot Spot": 70.5, "GPU Memory Junction": 80.0}


def test_a_gpu_that_wakes_up_resumes_its_readings_and_a_sleep_stops_them_again():
    driver = FakeDriver()
    provider = provider_for(driver)
    assert provider.get_temperatures()
    assert not provider.asleep

    driver.fall_asleep()
    assert provider.get_temperatures() == {}
    assert provider.asleep

    driver.wake_up()
    assert provider.get_temperatures()["GPU Hot Spot"] == 70.5
    assert not provider.asleep
    assert driver.name_calls == 1, "the setup is kept across a sleep"


def test_a_sleeping_gpu_is_not_an_error(caplog):
    driver = FakeDriver()
    driver.fall_asleep()

    with caplog.at_level(logging.DEBUG, logger=nvidia.__name__):
        provider = provider_for(driver)
        provider.get_temperatures()
        provider.get_temperatures()
        driver.wake_up()
        provider.get_temperatures()

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    moves = [record.message for record in caplog.records if "asleep" in record.message or "sleep" in record.message]
    assert moves == ["NVAPI: the GPU went to sleep"], "said once, when it changes"
    assert any("the GPU is awake" in record.message for record in caplog.records)


def test_another_refusal_of_the_state_call_does_not_stop_the_reads():
    """Only NOT_POWERED says the GPU is off. A driver without the call must not blind the source."""
    driver = FakeDriver()
    driver.pstate_error = nvapi.NvapiError("NVAPI has no interface 0x927DA4F6")

    provider = provider_for(driver)

    assert provider.get_temperatures()["GPU Hot Spot"] == 70.5
    assert not provider.asleep

    driver.pstate_error = nvapi.NvapiError("failed with NVAPI status -5", -5)
    assert provider.get_temperatures()["GPU Hot Spot"] == 70.5


def test_the_source_starts_awake():
    assert not NvidiaProvider().asleep


def test_a_refused_table_read_keeps_the_core():
    driver = FakeDriver()
    provider = provider_for(driver)
    provider.get_temperatures()
    driver.table_error = nvapi.NvapiError("busy")

    assert provider.get_temperatures() == {"GPU Core": 54.0}


def test_a_refused_core_read_keeps_the_table():
    driver = FakeDriver()
    driver.core_error = nvapi.NvapiError("busy")

    assert provider_for(driver).get_temperatures() == {"GPU Hot Spot": 70.5, "GPU Memory Junction": 80.0}


@pytest.mark.parametrize("failing", ["core_error", "table_error"])
def test_an_unexpected_error_from_the_driver_never_escapes(failing):
    driver = FakeDriver()
    provider = provider_for(driver)
    provider.get_temperatures()
    setattr(driver, failing, RuntimeError("ctypes said something odd"))

    assert isinstance(provider.get_temperatures(), dict)


def test_a_short_table_never_escapes():
    driver = FakeDriver(table=[raw(70.0)])

    assert provider_for(driver).get_temperatures() == {}


def test_two_threads_polling_at_once_set_up_once():
    """The control loop and the Settings panel poll from different worker threads."""
    driver = FakeDriver()
    created = []
    initialize = driver.initialize

    def slow_initialize():
        time.sleep(0.05)  # long enough for the second thread to arrive mid-setup
        initialize()

    driver.initialize = slow_initialize
    provider = NvidiaProvider(driver_factory=lambda: created.append(1) or driver)
    results = []

    threads = [threading.Thread(target=lambda: results.append(provider.get_temperatures())) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert driver.initialized == 1
    assert results == [{"GPU Core": 54.0, "GPU Hot Spot": 70.5, "GPU Memory Junction": 80.0}] * 4


def test_every_label_it_reports_can_be_tracked():
    """A label the plausibility check refused could be offered in Settings but never drive the fan."""
    readings = provider_for(FakeDriver()).get_temperatures()

    assert set(readings) == {"GPU Core", "GPU Hot Spot", "GPU Memory Junction"}
    assert all(is_plausible(label, value) for label, value in readings.items())


def test_it_is_registered_and_needs_no_administrator_rights(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: False)

    assert PROVIDERS["nvidia"] is NvidiaProvider
    assert find_provider("NVIDIA") is NvidiaProvider
    assert NvidiaProvider.label == "NVIDIA driver (NVAPI)"
    assert not lacks_admin_rights(NvidiaProvider)


def test_creating_the_source_does_not_load_the_library(monkeypatch):
    """Settings creates a source just to read its label, on a machine that may have no NVIDIA driver."""
    loads = []
    monkeypatch.setattr(nvapi.ctypes, "CDLL", lambda name: loads.append(name))

    assert isinstance(create_provider("nvidia"), NvidiaProvider)
    assert loads == []


# The ctypes layer, against DLL functions that are Python callbacks with the real signatures.


class FakeNvapiDll:
    """What ctypes.CDLL("nvapi64.dll") returns: nvapi_QueryInterface, handing out callback addresses."""

    def __init__(self):
        self.callbacks = {}
        self._keep_alive = []
        self.queried = []

        def query(interface_id):
            self.queried.append(interface_id)
            callback = self.callbacks.get(interface_id)
            return ctypes.cast(callback, ctypes.c_void_p).value if callback else None

        self.nvapi_QueryInterface = query

    def provide(self, interface_id, *argtypes):
        def register(function):
            callback = ctypes.CFUNCTYPE(ctypes.c_int, *argtypes)(function)
            self.callbacks[interface_id] = callback
            return function

        return register


@pytest.fixture
def dll(monkeypatch):
    fake = FakeNvapiDll()
    monkeypatch.setattr(nvapi.ctypes, "CDLL", lambda name: fake if name == "nvapi64.dll" else pytest.fail(name))
    return fake


def test_the_structures_have_the_sizes_the_driver_checks_in_the_version():
    assert ctypes.sizeof(nvapi._ThermalSensors) == 168
    assert ctypes.sizeof(nvapi._ThermalSettings) == 68
    assert nvapi._version(nvapi._ThermalSensors, 2) == 168 | (2 << 16)


def test_a_missing_dll_raises_from_the_constructor(monkeypatch):
    def refuse(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(nvapi.ctypes, "CDLL", refuse)

    with pytest.raises(OSError, match="nvapi64.dll"):
        nvapi.NvapiLibrary()


def test_an_interface_the_driver_lacks_is_an_error(dll):
    with pytest.raises(nvapi.NvapiError, match="0x0150E828"):
        nvapi.NvapiLibrary().initialize()


def test_a_negative_status_is_an_error(dll):
    dll.provide(nvapi.INTERFACE_INITIALIZE)(lambda: -3)

    with pytest.raises(nvapi.NvapiError, match="NvAPI_Initialize.*-3"):
        nvapi.NvapiLibrary().initialize()


def test_the_gpus_name_and_handles_come_through(dll):
    @dll.provide(nvapi.INTERFACE_ENUM_PHYSICAL_GPUS, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_int))
    def enum_gpus(handles, count):
        handles[0] = 0x1111
        handles[1] = 0x2222
        handles[2] = 0x3333  # beyond the count, so not a GPU
        count[0] = 2
        return 0

    @dll.provide(nvapi.INTERFACE_GET_FULL_NAME, ctypes.c_void_p, ctypes.c_void_p)
    def full_name(gpu, buffer):
        ctypes.memmove(buffer, RTX_4090.encode("ascii") + b"\0", len(RTX_4090) + 1)
        return 0

    library = nvapi.NvapiLibrary()

    assert library.physical_gpus() == [0x1111, 0x2222]
    assert library.gpu_name(0x1111) == RTX_4090


def test_the_table_is_sent_with_its_version_and_mask_and_read_back(dll):
    received = {}

    @dll.provide(nvapi.INTERFACE_GET_THERMAL_SENSORS, ctypes.c_void_p, ctypes.POINTER(nvapi._ThermalSensors))
    def thermal_sensors(gpu, sensors):
        received.update(gpu=gpu, version=sensors[0].version, mask=sensors[0].mask)
        if sensors[0].mask > 0xFF:
            return -5
        for slot in range(32):
            sensors[0].temperatures[slot] = slot * 256
        return 0

    library = nvapi.NvapiLibrary()

    table = library.thermal_sensors(0x1111, 0xFF)

    assert received == {"gpu": 0x1111, "version": 168 | (2 << 16), "mask": 0xFF}
    assert table == [slot * 256 for slot in range(32)]
    with pytest.raises(nvapi.NvapiError, match="-5"):
        library.thermal_sensors(0x1111, 0x1FF)


def test_the_state_call_reports_not_powered_by_its_status(dll):
    answer = {"status": 0, "pstate": 8}

    @dll.provide(nvapi.INTERFACE_GET_CURRENT_PSTATE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
    def current_pstate(gpu, pstate):
        pstate[0] = answer["pstate"]
        return answer["status"]

    library = nvapi.NvapiLibrary()
    assert library.current_pstate(0x1111) == 8

    answer["status"] = nvapi.STATUS_GPU_NOT_POWERED
    with pytest.raises(nvapi.NvapiError) as refused:
        library.current_pstate(0x1111)
    assert refused.value.status == -220


def test_the_core_is_the_sensor_aimed_at_the_gpu(dll):
    received = {}

    @dll.provide(
        nvapi.INTERFACE_GET_THERMAL_SETTINGS,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(nvapi._ThermalSettings),
    )
    def thermal_settings(gpu, target, settings):
        received.update(target=target, version=settings[0].version)
        settings[0].count = 2
        settings[0].sensor[0].target = 2  # memory
        settings[0].sensor[0].current_temp = 31
        settings[0].sensor[1].target = nvapi.THERMAL_TARGET_GPU
        settings[0].sensor[1].current_temp = 54
        return 0

    core = nvapi.NvapiLibrary().core_temperature(0x1111)

    assert core == 54.0
    assert received == {"target": nvapi.THERMAL_TARGET_ALL, "version": 68 | (2 << 16)}


def test_an_empty_answer_from_a_driver_polled_too_fast_is_no_core(dll):
    @dll.provide(
        nvapi.INTERFACE_GET_THERMAL_SETTINGS,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(nvapi._ThermalSettings),
    )
    def thermal_settings(gpu, target, settings):
        settings[0].count = 0
        return 0

    assert nvapi.NvapiLibrary().core_temperature(0x1111) is None


# The real driver. Skipped on a machine without one, like every e2e test.


@pytest.mark.e2e
def test_the_real_driver_agrees_with_nvidia_smi_without_administrator_rights(nvidia_smi):
    expected = nvidia_smi().get("GPU Core")
    if expected is None:
        pytest.skip("nvidia: needs an NVIDIA GPU with its driver installed, and nvidia-smi on the PATH")

    readings = NvidiaProvider().get_temperatures()

    assert "GPU Core" in readings, f"no core temperature in {readings}"
    assert abs(readings["GPU Core"] - expected) <= 10.0, (readings, expected)
    # The hot spot is the hottest point of the chip, so it cannot sit below the core by more than noise.
    if "GPU Hot Spot" in readings:
        assert readings["GPU Hot Spot"] >= readings["GPU Core"] - 5.0, readings
