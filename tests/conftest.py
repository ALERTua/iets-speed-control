"""Shared fixtures: raw WMI probes for the sensor e2e tests, and the one Tk root for widget tests.

The probes deliberately re-query WMI instead of reusing the providers, so an e2e test compares the
provider against an independently obtained answer rather than against itself.
"""

import base64
import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import fields
from pathlib import Path

import pytest
import pythoncom
from wmi import WMI

# The suite runs on the defaults, never on the developer's own config.yaml. The package loads its
# configuration file the moment it is imported, so the path has to point elsewhere before the first
# import below: then the developer's file is never even opened, a broken one cannot stop the suite,
# and a save() without an explicit path lands in this throwaway folder.
TEST_CONFIG_DIR = Path(tempfile.mkdtemp(prefix="iets-speed-control-tests-"))
os.environ["IETS_SPEED_CONTROL_CONFIG"] = str(TEST_CONFIG_DIR / "config.yaml")

from iets_speed_control.sensors import PROVIDERS  # imported only after the config path is set
from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG

pythoncom.CoInitialize()  # type: ignore[union-attr]

logger = logging.getLogger(__name__)


def pytest_unconfigure(config):
    shutil.rmtree(TEST_CONFIG_DIR, ignore_errors=True)
    if TEST_CONFIG_DIR.exists():
        # Something still holds a file in there; say so, or such folders pile up in %TEMP% unnoticed.
        sys.stderr.write(f"\nCould not remove the test configuration folder {TEST_CONFIG_DIR}\n")


def pytest_collection_modifyitems(items):
    """Mark every test that needs the shared Tk root as a GUI test.

    Derived from the fixtures a test asks for rather than written by hand, so a new widget test is
    marked whether or not its author remembers to. `just test-fast` uses the marker to leave the GUI
    tests out of its parallel run: Tk roots in several processes take the one keyboard focus from
    each other, so the GUI tests only ever run serially.
    """
    for item in items:
        if "tk_root" in getattr(item, "fixturenames", ()):
            item.add_marker("gui")


@pytest.fixture(autouse=True)
def clean_config():
    """Restore the live configuration after every test, section by section.

    Every module holds a reference to the CONFIG object itself, so it cannot be rebound; the sections
    inside it can, and that is enough to undo any edit a test made.
    """
    saved = cfg.copy(CONFIG)
    try:
        yield CONFIG
    finally:
        for section in fields(CONFIG):
            setattr(CONFIG, section.name, getattr(saved, section.name))


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


class FakeProvider:
    """A sensor source with known labels, so filter feedback has something predictable to find."""

    name = "fake"

    def __init__(self, readings=None):
        self.readings = readings or {"Fake CPU/Core Max": 61.0, "Fake CPU/Package": 58.5, "Fake GPU/Hot Spot": 44.0}

    def get_temperatures(self):
        return dict(self.readings)


@pytest.fixture(scope="module")
def settings_view(tk_root):
    """One SettingsView per test module.

    Building it costs ~0.25 s and destroying it ~1 s: 467 CustomTkinter widgets, and profiling shows
    almost all of that time inside Tcl tearing them down again. Per-test construction spent over a
    minute of the suite doing nothing else, so the `view` fixture resets this one instead.
    """
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.settings import SettingsView

    controller = SpeedController(sensor_provider=FakeProvider())
    widget = SettingsView(tk_root, controller)
    widget.pack(fill="both", expand=True)
    tk_root.update()
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


def reset_settings_view(view):
    """Put a shared SettingsView back to the state a freshly built one would be in.

    Anything a test can change has to be listed here, or the tests stop being independent of the
    order they run in. Runs after clean_config has restored the configuration, so the rows redisplay
    the original values.
    """
    from iets_speed_control.controller import Mode
    from iets_speed_control.gui.theme import HISTORY_WINDOW_SECONDS

    controller = view.controller
    controller.mode = Mode.AUTO
    controller.manual_speed = CONFIG.control.manual_speed
    controller._current_speed = 0
    controller.sensors = FakeProvider()
    controller.temp_window = CONFIG.control.temp_window
    controller.curve = CONFIG.control.curve

    for section in view.sections.values():
        for _path, refresh in section.bindings:
            refresh()
        for row in section.rows:
            row.clear_error()

    view.editor.set_points(CONFIG.control.curve, notify=False)
    view.editor._history.clear()
    view.editor.selected = 0
    view._initial_curve = controller.curve
    view._apply_lhm_visibility()
    view._apply_admin_note()
    view._show_filter_error(None)
    view.connection_label.configure(text="")
    view.history_window = HISTORY_WINDOW_SECONDS
    view.sync_mode()
    if view.search.get():
        view.search.delete(0, "end")
    view.show(view.SECTION_NAMES[0])


@pytest.fixture
def view(settings_view, clean_config, tk_root):
    """The shared SettingsView, reset to a known state for this test."""
    reset_settings_view(settings_view)
    tk_root.update()
    return settings_view


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


# Spelled out here rather than imported from the sensors package, so a wrong entry on either side
# shows up as a mismatch: the probes stay independent of the code they check.
NOT_LIVE_MARKERS = ("distance to tjmax", "limit", "resolution", "warning temperature", "critical temperature")


def is_live(label: str) -> bool:
    return not any(marker in label.lower() for marker in NOT_LIVE_MARKERS)


def probe_lhm() -> dict[str, float]:
    for namespace in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        try:
            sensors = WMI(namespace=namespace).Sensor(SensorType="Temperature")
        except Exception as e:  # noqa: BLE001 -- a missing WMI namespace surfaces as an arbitrary COM error
            logger.debug(f"LibreHardwareMonitor probe on {namespace} failed: {e}")
            continue

        if sensors:
            return {s.Name: float(s.Value) for s in sensors if is_live(s.Name)}

    return {}


def probe_lhm_web() -> dict[str, float]:
    settings = CONFIG.sensors.lhm_web
    request = urllib.request.Request(settings.url)
    if settings.username or settings.password:
        token = base64.b64encode(f"{settings.username}:{settings.password}".encode()).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
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

        if node.get("Type") != "Temperature" or not is_live(node.get("Text", "")):
            continue

        number = str(node.get("Value", "")).split()[0].replace(",", ".")
        try:
            value = float(number)
        except ValueError:
            continue

        hardware = trail[-2] if len(trail) >= 2 else ""
        found[f"{hardware}/{node.get('Text', '')}" if hardware else node.get("Text", "")] = value

    return found


def probe_lenovo_wmi() -> dict[str, float]:
    try:
        method = WMI(namespace="root\\WMI").LENOVO_OTHER_METHOD()[0]
    except Exception as e:  # noqa: BLE001 -- access denied and a missing class both surface as COM errors
        logger.debug(f"Lenovo WMI probe failed: {e}")
        return {}

    found = {}
    for label, capability in (("CPU", 0x05040000), ("GPU", 0x05050000), ("PCH", 0x05010000)):
        try:
            value = float(method.GetFeatureValue(IDs=capability)[0])
        except Exception as e:  # noqa: BLE001 -- one missing sensor must not hide the others
            logger.debug(f"Lenovo WMI probe has no {label}: {e}")
            continue
        if value > 0:
            found[label] = value

    return found


PROBES = {
    "aida64": probe_aida64,
    "lhm": probe_lhm,
    "lhm-web": probe_lhm_web,
    "lenovo-wmi": probe_lenovo_wmi,
}

SOURCE_HINTS = {
    "aida64": "AIDA64 must be running with 'write sensors to WMI' and temperature sensors enabled",
    "lhm": "LibreHardwareMonitor must be running as administrator so it registers its WMI provider",
    "lenovo-wmi": "needs a Lenovo Legion laptop and a test run as administrator",
    "lhm-web": "LibreHardwareMonitor must serve sensors.lhm_web.url (Options -> Remote Web Server -> Run)",
}


def use_this_machines_web_server():
    """Take sensors.lhm_web from the developer's own config.yaml, for the e2e tests only.

    The rest of the suite runs on the defaults, but the web server's address and credentials belong to
    the machine: on one whose server is not localhost:8085, the defaults would skip every lhm-web e2e
    test. Only that section is taken, and an unreadable file leaves the defaults in place. clean_config
    puts the defaults back after the test.
    """
    if not cfg.DEFAULT_CONFIG_PATH.exists():
        return

    try:
        own = cfg.load(cfg.DEFAULT_CONFIG_PATH)
    except cfg.ConfigError as e:
        logger.warning(f"Ignoring the developer's config for the e2e tests: {e}")
        return

    CONFIG.sensors.lhm_web = own.sensors.lhm_web


@pytest.fixture(params=sorted(PROVIDERS))
def provider_name(request) -> str:
    """Each registered sensor source, skipped when the underlying app is not publishing data."""
    name = request.param
    use_this_machines_web_server()
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
