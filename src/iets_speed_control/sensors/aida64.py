"""AIDA64 temperature source (requires AIDA64 running with WMI sensor export enabled)."""

import logging

from wmi import WMI

from .base import ensure_com_initialized, to_temperature

logger = logging.getLogger(__name__)


class Aida64Provider:
    """Reads AIDA64_SensorValues from the root\\WMI namespace."""

    name = "aida64"  # the code name: what goes in the configuration file
    label = "AIDA64"  # what a person reads in the settings panel
    WMI_NAMESPACE = "root\\WMI"
    TEMPERATURE_TYPE = "T"  # AIDA64 marks sensor kind with a single letter: T/F/V/P/C/S

    def get_temperatures(self) -> dict[str, float]:
        ensure_com_initialized()
        output: dict[str, float] = {}
        try:
            sensor_values = WMI(namespace=self.WMI_NAMESPACE).AIDA64_SensorValues()
        except Exception as e:  # noqa: BLE001 -- wmi/COM raise arbitrary types when AIDA64 is absent
            logger.error(f"Error connecting to AIDA64: {e}")
            return output

        for sensor in sensor_values:
            if "Type" in sensor.properties and sensor.wmi_property("Type").Value != self.TEMPERATURE_TYPE:
                continue

            temperature = to_temperature(sensor.wmi_property("Value").Value)
            if temperature is None:
                continue

            output[sensor.wmi_property("Label").Value] = temperature

        return output
