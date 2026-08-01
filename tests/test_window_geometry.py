"""Remembering the window's size and position, and refusing to restore a position off the desktop.

The desktop-bounds check is a pure function on purpose: monitor arrangements cannot be set up from a
test, but every case that matters can be described as a rectangle.
"""

from dataclasses import fields

import pytest

from iets_speed_control.gui.app import GEOMETRY, VISIBLE_MARGIN, desktop_bounds, is_on_desktop
from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG

ctk = pytest.importorskip("customtkinter")

ONE_SCREEN = (0, 0, 1920, 1080)
TWO_SCREENS = (-1920, 0, 1920, 1080)  # a second monitor to the left of the primary one


@pytest.fixture
def clean_config():
    saved = cfg.copy(CONFIG)
    try:
        yield CONFIG
    finally:
        for section in fields(CONFIG):
            setattr(CONFIG, section.name, getattr(saved, section.name))


# --- parsing what Tk reports ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("geometry", "expected"),
    [
        ("980x680+80+80", (980, 680, 80, 80)),
        ("900x600+0+0", (900, 600, 0, 0)),
        ("980x680-1900+40", (980, 680, -1900, 40)),  # window on a monitor left of the primary one
        ("980x680+80-25", (980, 680, 80, -25)),
    ],
)
def test_geometry_is_parsed_including_negative_offsets(geometry, expected):
    """Splitting on "+" silently loses a negative offset, and with it the whole saved position."""
    match = GEOMETRY.match(geometry)

    assert match is not None, f"{geometry!r} is a geometry Tk really reports"
    assert (int(match["width"]), int(match["height"]), int(match["x"]), int(match["y"])) == expected


@pytest.mark.parametrize("geometry", ["980x680", "+80+80", "", "980x680+80"])
def test_an_incomplete_geometry_is_rejected(geometry):
    assert GEOMETRY.match(geometry) is None


# --- is the saved position still usable ----------------------------------------------------------


def test_a_window_on_the_primary_screen_is_accepted():
    assert is_on_desktop(80, 80, 900, 600, ONE_SCREEN)


def test_a_window_on_a_second_screen_is_accepted():
    """Tk only reports the primary monitor, so this is the case a naive check gets wrong."""
    assert is_on_desktop(-1800, 100, 900, 600, TWO_SCREENS)


def test_a_position_from_an_unplugged_monitor_is_rejected():
    assert not is_on_desktop(-1800, 100, 900, 600, ONE_SCREEN)


def test_a_window_below_the_desktop_is_rejected():
    assert not is_on_desktop(80, 1080, 900, 600, ONE_SCREEN)


def test_a_window_with_its_title_bar_above_the_desktop_is_rejected():
    """A negative y puts the title bar out of reach, so the window can never be dragged back."""
    assert not is_on_desktop(80, -40, 900, 600, ONE_SCREEN)


def test_a_sliver_of_window_on_screen_is_not_enough():
    assert not is_on_desktop(1920 - VISIBLE_MARGIN + 1, 100, 900, 600, ONE_SCREEN)


def test_enough_window_on_screen_is_accepted():
    assert is_on_desktop(1920 - VISIBLE_MARGIN, 100, 900, 600, ONE_SCREEN)


def test_the_real_desktop_bounds_are_readable():
    bounds = desktop_bounds()

    assert bounds is not None, "on Windows the virtual screen metrics are always available"
    left, top, right, bottom = bounds
    assert right > left and bottom > top


# --- saving and restoring ------------------------------------------------------------------------


@pytest.fixture
def app(tk_root, clean_config):
    """A GUIApp using the shared test root as its window, with no tray and no asyncio thread."""
    from iets_speed_control.gui.app import GUIApp

    instance = GUIApp.__new__(GUIApp)
    instance.window = tk_root
    instance.rail = None
    instance.status_panel = None
    instance.settings_view = None
    return instance


def test_the_size_and_position_are_both_saved(app, tk_root, monkeypatch):
    saved = {}
    monkeypatch.setattr("iets_speed_control.gui.app.save_config", lambda config: saved.update(seen=True))
    tk_root.geometry("910x610+120+130")
    tk_root.update()

    app._save_window_geometry()

    assert (CONFIG.ui.window_width, CONFIG.ui.window_height) == (910, 610)
    assert (CONFIG.ui.window_x, CONFIG.ui.window_y) == (120, 130)
    assert saved, "the geometry has to reach the file, not just the config object"


def test_the_saved_size_is_restored(app, tk_root, monkeypatch):
    monkeypatch.setattr(CONFIG.ui, "window_width", 940)
    monkeypatch.setattr(CONFIG.ui, "window_height", 640)
    monkeypatch.setattr(CONFIG.ui, "window_x", None)
    monkeypatch.setattr(CONFIG.ui, "window_y", None)

    app._load_window_geometry()
    tk_root.update()

    assert GEOMETRY.match(tk_root.geometry())["width"] == "940"
    assert GEOMETRY.match(tk_root.geometry())["height"] == "640"


def test_a_position_off_the_desktop_is_not_restored(app, tk_root, monkeypatch):
    monkeypatch.setattr("iets_speed_control.gui.app.desktop_bounds", lambda: ONE_SCREEN)
    monkeypatch.setattr(CONFIG.ui, "window_width", 900)
    monkeypatch.setattr(CONFIG.ui, "window_height", 600)
    monkeypatch.setattr(CONFIG.ui, "window_x", -4000)
    monkeypatch.setattr(CONFIG.ui, "window_y", 100)
    tk_root.geometry("900x600+150+150")
    tk_root.update()

    app._load_window_geometry()
    tk_root.update()

    match = GEOMETRY.match(tk_root.geometry())
    assert int(match["x"]) != -4000, "restoring this would put the window where it cannot be reached"
    assert (match["width"], match["height"]) == ("900", "600"), "the size is still worth restoring"


def test_a_position_on_the_desktop_is_restored(app, tk_root, monkeypatch):
    monkeypatch.setattr("iets_speed_control.gui.app.desktop_bounds", lambda: ONE_SCREEN)
    monkeypatch.setattr(CONFIG.ui, "window_width", 900)
    monkeypatch.setattr(CONFIG.ui, "window_height", 600)
    monkeypatch.setattr(CONFIG.ui, "window_x", 140)
    monkeypatch.setattr(CONFIG.ui, "window_y", 160)

    was = tk_root.geometry()
    try:
        app._load_window_geometry()
        tk_root.update()

        match = GEOMETRY.match(tk_root.geometry())
        assert (int(match["x"]), int(match["y"])) == (140, 160)
    finally:
        tk_root.geometry(was)
        tk_root.update()


def test_the_position_is_realized_before_anything_can_iconify_the_window(app, tk_root, monkeypatch):
    """run() iconifies the window straight after loading the geometry.

    Windows discards a position request Tk has not applied yet and cascades the window to its own
    default spot, while honouring the size -- which looks exactly like "the size is restored but the
    position is not". So the request has to be realized here, without waiting for the main loop.
    """
    monkeypatch.setattr("iets_speed_control.gui.app.desktop_bounds", lambda: ONE_SCREEN)
    monkeypatch.setattr(CONFIG.ui, "window_width", 880)
    monkeypatch.setattr(CONFIG.ui, "window_height", 580)
    monkeypatch.setattr(CONFIG.ui, "window_x", 260)
    monkeypatch.setattr(CONFIG.ui, "window_y", 180)

    was = tk_root.geometry()
    try:
        app._load_window_geometry()  # deliberately no update() of our own

        assert (tk_root.winfo_x(), tk_root.winfo_y()) == (260, 180)
    finally:
        tk_root.geometry(was)
        tk_root.update()


def test_nothing_saved_means_nothing_applied(app, tk_root, monkeypatch):
    for key in ("window_width", "window_height", "window_x", "window_y"):
        monkeypatch.setattr(CONFIG.ui, key, None)
    tk_root.geometry("700x460+200+200")
    tk_root.update()
    before = tk_root.geometry()

    app._load_window_geometry()
    tk_root.update()

    assert tk_root.geometry() == before


# --- minimising ----------------------------------------------------------------------------------


def test_minimising_hides_to_the_tray_when_that_is_on(app, tk_root, monkeypatch):
    monkeypatch.setattr(CONFIG.ui, "hide_to_tray_on_minimize", True)
    monkeypatch.setattr(tk_root, "state", lambda: "iconic")
    scheduled = []
    monkeypatch.setattr(tk_root, "after", lambda delay, callback: scheduled.append(callback))

    app._on_minimize(event=None)

    assert scheduled, "minimising should schedule the hide"


def test_minimising_leaves_the_window_alone_when_that_is_off(app, tk_root, monkeypatch):
    monkeypatch.setattr(CONFIG.ui, "hide_to_tray_on_minimize", False)
    monkeypatch.setattr(tk_root, "state", lambda: "iconic")
    scheduled = []
    monkeypatch.setattr(tk_root, "after", lambda delay, callback: scheduled.append(callback))

    app._on_minimize(event=None)

    assert not scheduled, "with the switch off the window stays in the taskbar"


def test_startup_goes_to_the_tray_when_that_is_on(app, tk_root, monkeypatch):
    monkeypatch.setattr(CONFIG.ui, "minimize_on_launch", True)
    tk_root.deiconify()
    tk_root.update()

    try:
        app._present_window()
        tk_root.update()

        assert tk_root.state() == "iconic"
    finally:
        tk_root.deiconify()
        tk_root.update()


def test_startup_shows_the_window_when_that_is_off(app, tk_root, monkeypatch):
    monkeypatch.setattr(CONFIG.ui, "minimize_on_launch", False)
    tk_root.iconify()
    tk_root.update()

    try:
        app._present_window()
        tk_root.update()

        assert tk_root.state() == "normal"
    finally:
        tk_root.deiconify()
        tk_root.update()


def test_both_minimise_settings_default_to_on():
    reference = cfg.defaults()

    assert reference.ui.minimize_on_launch is True
    assert reference.ui.hide_to_tray_on_minimize is True


# --- the configuration keys ----------------------------------------------------------------------


def test_the_size_keys_default_to_unset():
    reference = cfg.defaults()

    assert reference.ui.window_width is None
    assert reference.ui.window_height is None


@pytest.mark.parametrize("key", ["window_width", "window_height"])
def test_a_nonsense_size_is_rejected(key):
    config = cfg.defaults()
    setattr(config.ui, key, 0)

    with pytest.raises(cfg.ConfigError, match=f"ui.{key} must be greater than zero"):
        cfg.validate(config)


def test_only_a_changed_size_is_written():
    config = cfg.defaults()
    config.ui.window_width = 940

    sparse = cfg.to_sparse(config)

    assert sparse == {"ui": {"window_width": 940}}
