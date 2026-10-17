"""The code names of the sources, and the other spellings sensors.tracked accepts for them.

Kept apart from the sensors package on purpose: the configuration needs it while it loads, and the
sensors package imports the configuration, so taking it from there would be an import cycle. A test
checks that these names are exactly the registered sources, and that every alias points at one.
"""

SOURCE_NAMES = ("aida64", "lhm", "lhm-web", "lenovo-wmi", "nvidia")

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
