"""Shared fixtures: raw WMI probes for the sensor e2e tests, and the one Tk root for widget tests.

The probes deliberately re-query WMI instead of reusing the providers, so an e2e test compares the
provider against an independently obtained answer rather than against itself.
"""

import json
import logging
import urllib.request

import pytest
import pythoncom
from wmi import WMI

from iets_speed_control.sensors import PROVIDERS
from iets_speed_control.util import env

pythoncom.CoInitialize()  # type: ignore[union-attr]

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def tk_root():
    """One mapped-but-invisible Tk root for the entire test session.

    Two constraints forced this shape:
      * Creating a second CTk root after the first is destroyed makes Tk fail with "Can't find a
        usable init.tcl", so per-module roots silently turned widget tests into skips.
      * Tk does not deliver synthesized mouse events to a withdrawn window, so the root has to stay
        mapped. Zero alpha keeps it off the screen while remaining mapped.
    """
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001 -- any Tk/display failure means "cannot test widgets here"
        pytest.skip(f"no Tk display available: {e}")

    root.geometry("700x460")
    root.attributes("-alpha", 0.0)
    root.update()
    try:
        yield root
    finally:
        root.destroy()


def probe_aida64() -> dict[str, float]:
    try:
        sensors = WMI(namespace="root\\WMI").AIDA64_SensorValues()
    except Exception as e:  # noqa: BLE001 -- wmi/COM raise arbitrary types when AIDA64 is absent
        logger.debug(f"AIDA64 probe failed: {e}")
        return {}

    return {
        s.wmi_property("Label").Value: float(s.wmi_property("Value").Value)
        for s in sensors
        if s.wmi_property("Type").Value == "T"
    }


def probe_lhm() -> dict[str, float]:
    for namespace in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        try:
            sensors = WMI(namespace=namespace).Sensor(SensorType="Temperature")
        except Exception as e:  # noqa: BLE001 -- a missing WMI namespace surfaces as an arbitrary COM error
            logger.debug(f"LibreHardwareMonitor probe on {namespace} failed: {e}")
            continue

        if sensors:
            return {s.Name: float(s.Value) for s in sensors}

    return {}


def probe_lhm_web() -> dict[str, float]:
    try:
        with urllib.request.urlopen(env.LHM_WEB_URL, timeout=env.LHM_WEB_TIMEOUT) as response:
            document = json.load(response)
    except Exception as e:  # noqa: BLE001 -- any transport or decode failure just means "unavailable"
        logger.debug(f"LibreHardwareMonitor web probe failed: {e}")
        return {}

    found: dict[str, float] = {}
    stack = [(document, [])]
    while stack:
        node, trail = stack.pop()
        children = node.get("Children") or []
        if children:
            stack.extend((child, trail + [node.get("Text", "")]) for child in children)
            continue

        if node.get("Type") != "Temperature":
            continue

        number = str(node.get("Value", "")).split()[0].replace(",", ".")
        try:
            value = float(number)
        except ValueError:
            continue

        hardware = trail[-2] if len(trail) >= 2 else ""
        found[f"{hardware}/{node.get('Text', '')}" if hardware else node.get("Text", "")] = value

    return found


PROBES = {
    "aida64": probe_aida64,
    "lhm": probe_lhm,
    "lhm-web": probe_lhm_web,
}

SOURCE_HINTS = {
    "aida64": "AIDA64 must be running with 'write sensors to WMI' and temperature sensors enabled",
    "lhm": "LibreHardwareMonitor must be running as administrator so it registers its WMI provider",
    "lhm-web": f"LibreHardwareMonitor must serve {env.LHM_WEB_URL} (Options -> Remote Web Server -> Run)",
}


@pytest.fixture(params=sorted(PROVIDERS))
def provider_name(request) -> str:
    """Each registered sensor source, skipped when the underlying app is not publishing data."""
    name = request.param
    if not PROBES[name]():
        pytest.skip(f"{name}: no temperatures available -- {SOURCE_HINTS[name]}")

    return name


@pytest.fixture
def probe(provider_name):
    """Callable that reads temperatures straight from the source, bypassing the provider."""
    return PROBES[provider_name]


@pytest.fixture
def raw_temperatures(probe) -> dict[str, float]:
    """One snapshot taken directly from the source, bypassing the provider under test."""
    return probe()
