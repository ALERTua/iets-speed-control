"""NVIDIA GPU temperature source: the display driver's own NVAPI library.

Gives the core, the hot spot and the memory junction temperature without LibreHardwareMonitor or AIDA64
running, and without administrator rights: nvapi64.dll answers any process. The hot spot and the memory
junction come from an undocumented NVAPI call that LibreHardwareMonitor also uses, with the same labels,
so a filter written for one works for the other. Only the first GPU is read.

On a laptop with switchable graphics the NVIDIA GPU is powered off while nothing uses it, and the
thermal calls power it on again: a poll every few seconds would keep it awake at about 12 W. So each
poll first asks for the GPU's performance state, the check other monitors rely on as not waking it, and reads nothing else while
the driver says the GPU is not powered. The sensor list and the one-time setup that needs the GPU wait
for the first poll that finds it awake.
"""

import logging
import threading
import time
from collections.abc import Callable

from .nvapi import STATUS_GPU_NOT_POWERED, NvapiDriver, NvapiError, NvapiLibrary

logger = logging.getLogger(__name__)

MASK_BITS = 32
# The undocumented table is in 1/256 °C.
RAW_PER_DEGREE = 256


class NvidiaProvider:
    """Reads GPU Core, GPU Hot Spot and GPU Memory Junction from the first NVIDIA GPU."""

    name = "nvidia"
    label = "NVIDIA driver (NVAPI)"
    requires_admin = False
    # A failed setup is tried again after this long, so a driver that was still loading at start-up is
    # found later, without a line in the log on every one-second poll.
    SETUP_RETRY_SECONDS = 60.0

    def __init__(
        self,
        driver_factory: Callable[[], NvapiDriver] = NvapiLibrary,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._driver_factory = driver_factory
        self._clock = clock
        # True while the driver says the GPU is switched off. A sleeping GPU is normal, not an error:
        # the readings are simply empty.
        self.asleep = False
        # Both the control loop and the Settings panel read from worker threads, and the driver handles
        # and the lazy setup are shared state.
        self._lock = threading.Lock()
        self._driver: NvapiDriver | None = None
        self._gpu = 0
        self._identified = False
        self._slots: dict[str, int] = {}
        self._mask = 0
        self._setup_failed_at: float | None = None
        self._told_about_failure = False

    def get_temperatures(self) -> dict[str, float]:
        with self._lock:
            try:
                return self._read()
            except Exception as e:  # noqa: BLE001 -- the loop must survive any driver or ctypes failure
                logger.debug(f"NVAPI read failed: {e}")
                return {}

    def _read(self) -> dict[str, float]:
        if self._driver is None and not self._attempt(self._connect):
            return {}

        driver = self._driver
        assert driver is not None
        if self._gpu_is_asleep(driver):
            return {}

        if not self._identified and not (self._attempt(self._identify) and self._identified):
            return {}

        output: dict[str, float] = {}

        # The documented sensor can fail or answer "nothing yet" on its own, which must not hide the others.
        if "GPU Core" not in self._slots:
            try:
                core = driver.core_temperature(self._gpu)
            except OSError as e:
                logger.debug(f"NVAPI has no GPU Core temperature: {e}")
                core = None
            if core:
                output["GPU Core"] = core

        if not self._mask:
            return output

        try:
            table = driver.thermal_sensors(self._gpu, self._mask)
        except OSError as e:
            logger.debug(f"NVAPI has no thermal sensor table: {e}")
            return output

        for label, slot in self._slots.items():
            # No live chip reads 0 °C, so a zero slot is a sensor this GPU lacks rather than a cold one.
            if table[slot] > 0:
                output[label] = table[slot] / RAW_PER_DEGREE

        return output

    def _gpu_is_asleep(self, driver: NvapiDriver) -> bool:
        try:
            driver.current_pstate(self._gpu)
        except NvapiError as e:
            # Any other refusal says nothing about power, so the reads below are tried as usual.
            asleep = e.status == STATUS_GPU_NOT_POWERED
        else:
            asleep = False

        if asleep != self.asleep:
            logger.debug("NVAPI: the GPU went to sleep" if asleep else "NVAPI: the GPU is awake")
            self.asleep = asleep

        return asleep

    def _attempt(self, step: Callable[[], None]) -> bool:
        """Run one setup step. A failure is logged once, and the step is not retried for a minute."""
        now = self._clock()
        if self._setup_failed_at is not None and now - self._setup_failed_at < self.SETUP_RETRY_SECONDS:
            return False

        try:
            step()
        except Exception as e:  # noqa: BLE001 -- a missing DLL, a refused call and a ctypes error all mean "no source"
            self._setup_failed_at = now
            # Said once, like the other sources: the loop polls every second, and each later retry would repeat it.
            if not self._told_about_failure:
                logger.error(f"Cannot read the NVIDIA driver (nvapi64.dll): {e}. Is an NVIDIA GPU installed?")
                self._told_about_failure = True
            else:
                logger.debug(f"NVAPI setup failed again: {e}")
            return False

        self._setup_failed_at = None
        return True

    def _connect(self) -> None:
        """Load the library and find the GPU. Neither wakes a sleeping GPU."""
        driver = self._driver_factory()
        driver.initialize()
        gpus = driver.physical_gpus()
        if not gpus:
            raise OSError("the driver reports no NVIDIA GPU")

        self._gpu = gpus[0]
        self._driver = driver

    def _identify(self) -> None:
        """Learn the GPU's name, which fixes its sensor layout, and its usable mask. Needs the GPU awake."""
        driver = self._driver
        assert driver is not None
        gpu_name = driver.gpu_name(self._gpu)
        mask = find_mask(driver, self._gpu)
        if mask is None:
            # Not a failure: the next poll that finds the GPU awake identifies it again from the start.
            logger.debug("NVAPI: the GPU went to sleep while its sensors were being found")
            self.asleep = True
            return

        # Without a mask there is no table to read slots from.
        self._slots = sensor_slots(gpu_name) if mask else {}
        self._mask = mask
        self._identified = True
        logger.debug(f"NVAPI: {gpu_name}, thermal sensor mask 0x{mask:08X}")


def sensor_slots(gpu_name: str) -> dict[str, int]:
    """The slot of each label in the undocumented table, which differs by GPU series.

    As LibreHardwareMonitor has them. An RTX 50 has no hot spot, and its core sits in slot 1 instead of
    in the documented sensor.
    """
    name = gpu_name.upper()
    if name.startswith("NVIDIA GEFORCE RTX 50"):
        return {"GPU Core": 1, "GPU Memory Junction": 2}

    if name.startswith("NVIDIA GEFORCE RTX 40"):
        return {"GPU Hot Spot": 1, "GPU Memory Junction": 7}

    return {"GPU Hot Spot": 1, "GPU Memory Junction": 9}


def find_mask(driver: NvapiDriver, gpu: int) -> int | None:
    """The mask that asks for every sensor this GPU has, or 0 when it has no table at all.

    The driver refuses a mask that names a sensor it lacks, so the mask grows one bit at a time until
    the first refusal. None means the GPU went to sleep during the search, so its answer is unknown.
    """
    mask = 0
    for bit in range(MASK_BITS):
        try:
            driver.thermal_sensors(gpu, 1 << bit)
        except NvapiError as e:
            # A GPU that fell asleep mid-search refuses every mask, which says nothing about its sensors.
            if e.status == STATUS_GPU_NOT_POWERED:
                return None
            break
        except OSError:
            break

        mask |= 1 << bit

    return mask
