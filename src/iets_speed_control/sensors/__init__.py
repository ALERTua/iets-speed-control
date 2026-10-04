"""Temperature sources. Pick one with the sensors.provider configuration key."""

from ..util.source_names import canonical_source
from .aida64 import Aida64Provider
from .base import SensorProvider, lacks_admin_rights
from .lenovo_wmi import LenovoWmiProvider
from .lhm import LibreHardwareMonitorProvider
from .lhm_web import LibreHardwareMonitorWebProvider

PROVIDERS = {
    Aida64Provider.name: Aida64Provider,
    LibreHardwareMonitorProvider.name: LibreHardwareMonitorProvider,
    LibreHardwareMonitorWebProvider.name: LibreHardwareMonitorWebProvider,
    LenovoWmiProvider.name: LenovoWmiProvider,
}

# Full names for the settings panel. The configuration file keeps the code names: they are short,
# stable and quotable in a bug report, and renaming a product would otherwise invalidate every
# existing config.
PROVIDER_LABELS = {name: provider.label for name, provider in PROVIDERS.items()}
PROVIDER_NAMES = {label: name for name, label in PROVIDER_LABELS.items()}


def find_provider(name: str) -> type[SensorProvider] | None:
    """The class of the temperature source named by sensors.provider, aliases included, or None."""
    return PROVIDERS.get(canonical_source(name))


def create_provider(name: str) -> SensorProvider:
    """Instantiate the temperature source named by sensors.provider."""
    provider = find_provider(name)
    if provider is None:
        raise ValueError(f"Unknown sensors.provider {name!r}. Available: {', '.join(sorted(PROVIDERS))}.")

    return provider()


__all__ = [
    "PROVIDERS",
    "PROVIDER_LABELS",
    "PROVIDER_NAMES",
    "Aida64Provider",
    "LenovoWmiProvider",
    "LibreHardwareMonitorProvider",
    "LibreHardwareMonitorWebProvider",
    "SensorProvider",
    "create_provider",
    "find_provider",
    "lacks_admin_rights",
]
