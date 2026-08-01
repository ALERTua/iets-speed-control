"""Temperature sources. Pick one with the sensors.provider configuration key."""

from .aida64 import Aida64Provider
from .base import SensorProvider
from .lhm import LibreHardwareMonitorProvider
from .lhm_web import LibreHardwareMonitorWebProvider

PROVIDERS = {
    Aida64Provider.name: Aida64Provider,
    LibreHardwareMonitorProvider.name: LibreHardwareMonitorProvider,
    LibreHardwareMonitorWebProvider.name: LibreHardwareMonitorWebProvider,
}

# Full names for the settings panel. The configuration file keeps the code names: they are short,
# stable and quotable in a bug report, and renaming a product would otherwise invalidate every
# existing config.
PROVIDER_LABELS = {name: provider.label for name, provider in PROVIDERS.items()}
PROVIDER_NAMES = {label: name for name, label in PROVIDER_LABELS.items()}

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
    """Instantiate the temperature source named by sensors.provider."""
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    provider = PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f"Unknown sensors.provider {name!r}. Available: {', '.join(sorted(PROVIDERS))}.")

    return provider()


__all__ = [
    "PROVIDERS",
    "PROVIDER_LABELS",
    "PROVIDER_NAMES",
    "Aida64Provider",
    "LibreHardwareMonitorProvider",
    "LibreHardwareMonitorWebProvider",
    "SensorProvider",
    "create_provider",
]
