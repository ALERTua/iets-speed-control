"""Lenovo Legion temperature source: the laptop's embedded controller, through Lenovo's own WMI class.

Lenovo Legion Toolkit reads its CPU and GPU temperatures from the same class, so this needs no
monitoring app running at all. The class answers only to an elevated process: without administrator
rights Windows refuses the query outright, so this source needs the app itself to run as administrator.
"""

import logging

from wmi import WMI

from .base import ensure_com_initialized, is_elevated, to_temperature

logger = logging.getLogger(__name__)

# Capability IDs that LENOVO_OTHER_METHOD.GetFeatureValue takes, as Lenovo Legion Toolkit names them.
CAPABILITIES = {
    "CPU": 0x05040000,
    "GPU": 0x05050000,
    "PCH": 0x05010000,
}


class LenovoWmiProvider:
    """Reads the CPU, GPU and chipset (PCH) temperatures from LENOVO_OTHER_METHOD."""

    name = "lenovo-wmi"
    label = "Lenovo Legion (WMI)"
    requires_admin = True
    WMI_NAMESPACE = "root\\WMI"

    def get_temperatures(self) -> dict[str, float]:
        output: dict[str, float] = {}
        if not is_elevated():
            logger.error(
                "Lenovo WMI answers only to administrators. Run IETS Speed Control as administrator"
                " to read temperatures from it."
            )
            return output

        ensure_com_initialized()
        try:
            methods = WMI(namespace=self.WMI_NAMESPACE).LENOVO_OTHER_METHOD()
        except Exception as e:  # noqa: BLE001 -- a missing class surfaces as an arbitrary COM error
            logger.error(f"Error connecting to Lenovo WMI (LENOVO_OTHER_METHOD): {e}. Is this a Lenovo Legion laptop?")
            return output

        if not methods:
            logger.error("Lenovo WMI has no LENOVO_OTHER_METHOD instance. Is this a Lenovo Legion laptop?")
            return output

        method = methods[0]
        for label, capability in CAPABILITIES.items():
            try:
                (value,) = method.GetFeatureValue(IDs=capability)
            except Exception as e:  # noqa: BLE001 -- a model without this sensor fails the call, not the rest
                logger.debug(f"Lenovo WMI has no {label} temperature: {e}")
                continue

            temperature = to_temperature(value)
            # No live chip reads 0 °C, so a zero is a missing sensor rather than a cold one.
            if temperature is None or temperature <= 0:
                continue

            output[label] = temperature

        return output
