"""Temperature sources. Pick one via the SENSOR_PROVIDER environment variable."""

from .aida64 import Aida64Provider
from .base import SensorProvider
from .lhm import LibreHardwareMonitorProvider
from .lhm_web import LibreHardwareMonitorWebProvider

PROVIDERS = {
    Aida64Provider.name: Aida64Provider,
    LibreHardwareMonitorProvider.name: LibreHardwareMonitorProvider,
    LibreHardwareMonitorWebProvider.name: LibreHardwareMonitorWebProvider,
}

ALIASES = {
    "aida": Aida64Provider.name,
    "librehardwaremonitor": LibreHardwareMonitorProvider.name,
    "openhardwaremonitor": LibreHardwareMonitorProvider.name,
    "ohm": LibreHardwareMonitorProvider.name,
    "lhm_web": LibreHardwareMonitorWebProvider.name,
    "lhmweb": LibreHardwareMonitorWebProvider.name,
    "lhm-http": LibreHardwareMonitorWebProvider.name,
}


def create_provider(name: str) -> SensorProvider:
    """Instantiate the temperature source named by SENSOR_PROVIDER."""
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    provider = PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f"Unknown SENSOR_PROVIDER {name!r}. Available: {', '.join(sorted(PROVIDERS))}.")

    return provider()


__all__ = [
    "PROVIDERS",
    "Aida64Provider",
    "LibreHardwareMonitorProvider",
    "LibreHardwareMonitorWebProvider",
    "SensorProvider",
    "create_provider",
]
