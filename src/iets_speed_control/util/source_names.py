"""The other spellings sensors.provider accepts, and the code name each one means.

Kept apart from the sensors package on purpose: the configuration needs it while it loads, and the
sensors package imports the configuration, so taking it from there would be an import cycle. A test
checks that every alias here points at a registered source.
"""

ALIASES = {
    "aida": "aida64",
    "librehardwaremonitor": "lhm",
    "openhardwaremonitor": "lhm",
    "ohm": "lhm",
    "lhm_web": "lhm-web",
    "lhmweb": "lhm-web",
    "lhm-http": "lhm-web",
    "lenovo_wmi": "lenovo-wmi",
    "lenovo": "lenovo-wmi",
}


def canonical_source(name: str) -> str:
    """The code name for any accepted spelling of a source; unknown names come back normalised."""
    key = (name or "").strip().lower()
    return ALIASES.get(key, key)
