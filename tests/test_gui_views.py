"""The navigation rail and the two destinations it switches between."""

import pytest

from iets_speed_control.gui.nav import NavigationRail, NavItem
from iets_speed_control.gui.theme import RAIL_COLLAPSED_WIDTH, RAIL_EXPANDED_WIDTH


@pytest.fixture
def app(tk_root):
    """A GUIApp wired to the shared test root, with no tray and no asyncio thread."""
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.app import NAV_ITEMS, GUIApp
    from iets_speed_control.gui.settings import SettingsView
    from iets_speed_control.gui.status import StatusPanel
    from iets_speed_control.gui.theme import HISTORY_WINDOW_SECONDS

    instance = GUIApp.__new__(GUIApp)  # skip __init__: it would build icons and a tray stack
    instance.controller = SpeedController()
    instance.tray_icon = None
    instance.loop = None
    instance._running = False
    instance.history_window = HISTORY_WINDOW_SECONDS
    instance.view = "home"
    instance.window = tk_root

    instance.rail = NavigationRail(tk_root, NAV_ITEMS, on_select=instance._on_nav_select)
    instance.rail.pack(side="left", fill="y")
    content = __import__("customtkinter").CTkFrame(tk_root, fg_color="transparent")
    content.pack(side="left", fill="both", expand=True)

    instance.status_panel = StatusPanel(content, instance.controller, instance)
    instance.status_panel.stop_polling()
    instance.settings_view = SettingsView(
        content,
        instance.controller,
        on_history_window=instance._on_history_window,
        history_window=instance.history_window,
    )
    instance.status_panel.pack(fill="both", expand=True)
    instance.rail.select("home", notify=False)
    tk_root.update()

    try:
        yield instance
    finally:
        instance.rail.destroy()
        content.destroy()
        tk_root.update()


@pytest.fixture
def rail(tk_root):
    chosen = []
    items = [
        NavItem("home", "Home", "H"),
        NavItem("settings", "Settings", "S"),
        NavItem("settings:curve", "Curve", parent="settings"),
        NavItem("settings:display", "Display", parent="settings"),
    ]
    widget = NavigationRail(tk_root, items, on_select=chosen.append)
    widget.pack(side="left", fill="y")
    widget.chosen = chosen
    tk_root.update()
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


def packed(rail):
    return [key for key, button in rail.buttons.items() if button.winfo_manager()]


# --- the rail itself ----------------------------------------------------------------------------


def test_top_level_items_are_always_listed(rail):
    assert "home" in packed(rail)
    assert "settings" in packed(rail)


def test_sub_items_appear_only_under_the_active_parent(rail):
    rail.select("home")
    assert packed(rail) == ["home", "settings"], "sub-items must stay hidden under another parent"

    rail.select("settings")
    assert "settings:curve" in packed(rail)
    assert "settings:display" in packed(rail)


def test_selecting_a_sub_item_keeps_its_siblings_visible(rail):
    rail.select("settings:display")

    assert "settings:curve" in packed(rail)
    assert rail.selected == "settings:display"


def test_collapsing_narrows_the_rail_and_hides_sub_items(rail):
    rail.select("settings")
    rail.toggle()

    assert rail.collapsed
    assert rail.cget("width") == RAIL_COLLAPSED_WIDTH
    assert packed(rail) == ["home", "settings"], "a collapsed rail has no room for sub-items"


def test_collapsed_items_show_the_icon_only(rail):
    rail.toggle()

    assert rail.buttons["home"].cget("text") == "H"
    assert "Home" not in rail.buttons["home"].cget("text")


def test_expanding_restores_labels_and_width(rail):
    rail.toggle()
    rail.toggle()

    assert not rail.collapsed
    assert rail.cget("width") == RAIL_EXPANDED_WIDTH
    assert "Home" in rail.buttons["home"].cget("text")


def test_the_active_item_is_highlighted(rail):
    rail.select("settings")

    assert rail.buttons["settings"].cget("fg_color") != "transparent"
    assert rail.buttons["home"].cget("fg_color") == "transparent"


def test_selecting_notifies_once(rail):
    rail.select("settings")
    rail.select("home")

    assert rail.chosen == ["settings", "home"]


def test_select_can_stay_silent(rail):
    rail.select("home", notify=False)

    assert rail.chosen == []
    assert rail.selected == "home"


# --- the two destinations -----------------------------------------------------------------------


def test_rail_switches_between_home_and_settings(app):
    app.rail.select("settings")
    app.window.update()

    assert app.view == "settings"
    assert app.settings_view.winfo_ismapped()
    assert not app.status_panel.winfo_ismapped()

    app.rail.select("home")
    app.window.update()

    assert app.view == "home"
    assert app.status_panel.winfo_ismapped()
    assert not app.settings_view.winfo_ismapped()


def test_a_sub_item_opens_settings_on_that_section(app):
    app.rail.select("settings:Display")
    app.window.update()

    assert app.view == "settings"
    assert app.settings_view.active == "Display"


def test_no_second_window_is_created(app):
    app.rail.select("settings")
    app.window.update()
    app.rail.select("home")
    app.window.update()

    assert not any(child.winfo_class() == "Toplevel" for child in app.window.winfo_children())


def test_window_size_does_not_change_with_the_destination(app):
    before = (app.window.winfo_width(), app.window.winfo_height())

    app.rail.select("settings")
    app.window.update()

    assert (app.window.winfo_width(), app.window.winfo_height()) == before


def test_mode_switch_resyncs_when_returning_home(app):
    from iets_speed_control.controller import Mode

    app.rail.select("settings")
    app.window.update()
    app.controller.mode = Mode.MANUAL  # as if changed inside the settings view

    app.rail.select("home")
    app.window.update()

    assert app.status_panel.mode_switch.get() == "Manual"


def test_history_window_change_reaches_the_graph(app):
    app._on_history_window(1800)

    assert app.history_window == 1800
    assert app.status_panel.history.window_seconds == 1800
