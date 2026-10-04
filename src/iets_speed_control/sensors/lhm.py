"""LibreHardwareMonitor temperature source (free alternative to AIDA64).

Requires LibreHardwareMonitor running as administrator so that it registers its WMI provider.
"""

import logging

from wmi import WMI

from .base import ensure_com_initialized, is_live_temperature, to_temperature

logger = logging.getLogger(__name__)


class LibreHardwareMonitorProvider:
    """Reads Sensor instances of type Temperature from the LibreHardwareMonitor WMI namespace."""

    name = "lhm"
    label = "LibreHardwareMonitor (WMI)"
    # OpenHardwareMonitor exposes an identical Sensor schema, so it works as a fallback.
    WMI_NAMESPACES = ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor")
    SENSOR_TYPE = "Temperature"

    def get_temperatures(self) -> dict[str, float]:
        ensure_com_initialized()
        output: dict[str, float] = {}
        sensors = None
        for namespace in self.WMI_NAMESPACES:
            try:
                sensors = WMI(namespace=namespace).Sensor(SensorType=self.SENSOR_TYPE)
            except Exception as e:  # noqa: BLE001 -- a missing WMI namespace surfaces as an arbitrary COM error
                logger.debug(f"LibreHardwareMonitor namespace {namespace} unavailable: {e}")
                continue

            if sensors:
                logger.debug(f"Reading temperatures from {namespace}")
                break

            logger.debug(f"LibreHardwareMonitor namespace {namespace} exposes no temperature sensors")

        if not sensors:
            logger.error(
                "Error connecting to LibreHardwareMonitor: no temperature sensors found."
                " Is it running as administrator?"
            )
            return output

        for sensor in sensors:
            if not is_live_temperature(sensor.Name):
                continue

            temperature = to_temperature(sensor.Value)
            if temperature is None:
                continue

            label = sensor.Name
            if label in output:  # several hardware items can expose the same sensor name
                label = f"{label} ({sensor.Identifier})"

            output[label] = temperature

        return output
