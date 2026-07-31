"""Tray application: window, tray icon and the asyncio thread the controller runs on."""

import asyncio
import json
import logging
import threading
from pathlib import Path
from tkinter import TclError

import customtkinter as ctk
import pystray
from PIL import Image
from pystray import MenuItem as Item

from ..controller import SpeedController
from .nav import NavigationRail, NavItem
from .settings import SettingsView
from .status import StatusPanel
from .theme import APP_NAME, BACKGROUND, HISTORY_WINDOW_SECONDS, WINDOW_MIN_SIZE, WINDOW_SIZE

HOME = "home"
SETTINGS = "settings"

NAV_ITEMS = [
    NavItem(HOME, "Home", "⌂"),
    NavItem(SETTINGS, "Settings", "⚙"),
    *[NavItem(f"settings:{name}", name, parent=SETTINGS) for name in SettingsView.SECTION_NAMES],
]

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).parent.parent.parent.parent / "media"
WINDOW_STATE_FILE = Path.home() / ".iets-speed-control" / "config.json"


class GUIApp:
    """Main application with tray icon and GUI window."""

    def __init__(self):
        self.controller = SpeedController()
        self.window: ctk.CTk | None = None
        self.rail: NavigationRail | None = None
        self.status_panel: StatusPanel | None = None
        self.settings_view: SettingsView | None = None
        self.view = HOME
        self.tray_icon: pystray.Icon | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self.history_window = HISTORY_WINDOW_SECONDS

        self.icon_path = MEDIA_DIR / "icon.ico"
        self.icon_image = self._load_icon(self.icon_path)
        self.icon_red_image = self._load_icon(MEDIA_DIR / "icon-red.ico")
        self._is_connected = True

    # --- icons ----------------------------------------------------------------------------

    def _load_icon(self, icon_path: Path) -> Image.Image | None:
        try:
            if icon_path.exists():
                return Image.open(icon_path)
            return Image.new("RGB", (64, 64), color="blue")
        except (OSError, ValueError) as e:
            logger.error(f"Failed to load icon: {e}")
            return Image.new("RGB", (64, 64), color="blue")

    def update_tray_icon(self, connected: bool):
        if connected == self._is_connected:
            return

        self._is_connected = connected
        if self.tray_icon:
            self.tray_icon.icon = self.icon_image if connected else self.icon_red_image

    # --- tray -----------------------------------------------------------------------------

    def _create_tray_icon(self) -> pystray.Icon:
        menu = pystray.Menu(
            Item("Show", self._show_window, default=True),
            Item("Settings", self.show_settings),
            Item("Start", self._start_control),
            Item("Stop", self._stop_control),
            Item("Exit", self._exit_app),
        )
        return pystray.Icon(APP_NAME, self.icon_image, APP_NAME, menu)

    def _update_tray_tooltip(self):
        if self.tray_icon:
            self.tray_icon.title = (
                f"CPU: {self.controller.cpu_temp}°C | "
                f"GPU: {self.controller.gpu_temp}°C | "
                f"Fan: {self.controller.current_speed}%"
            )

    # --- windows --------------------------------------------------------------------------

    def _create_window(self):
        self.window = ctk.CTk(fg_color=BACKGROUND)
        self.window.title(APP_NAME)
        self.window.geometry("{}x{}".format(*WINDOW_SIZE))
        self.window.minsize(*WINDOW_MIN_SIZE)

        if self.icon_path.exists():
            self.window.iconbitmap(str(self.icon_path))

        self.rail = NavigationRail(self.window, NAV_ITEMS, on_select=self._on_nav_select)
        self.rail.pack(side="left", fill="y")

        content = ctk.CTkFrame(self.window, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True)

        # Both views live in the same content area; only one is packed at a time.
        self.status_panel = StatusPanel(content, self.controller, self)
        self.settings_view = SettingsView(
            content,
            self.controller,
            on_history_window=self._on_history_window,
            history_window=self.history_window,
        )
        self.status_panel.pack(fill="both", expand=True)
        self.rail.select(HOME, notify=False)

        self.window.protocol("WM_DELETE_WINDOW", self._exit_app)
        self.window.bind("<Unmap>", self._on_minimize)
        self._load_window_position()

    # --- navigation -----------------------------------------------------------------------

    def _on_nav_select(self, key):
        if key == HOME:
            self._switch(HOME)
        elif key == SETTINGS:
            self._switch(SETTINGS)
        elif key.startswith("settings:"):
            self._switch(SETTINGS)
            self.settings_view.show(key.split(":", 1)[1])

    def show_settings(self, icon=None, item=None):
        """Select the Settings destination. Safe to call from the tray thread."""
        if self.window:
            self.window.after(0, lambda: self.rail.select(SETTINGS))
            self._show_window()

    def show_home(self, icon=None, item=None):
        if self.window:
            self.window.after(0, lambda: self.rail.select(HOME))

    def _switch(self, view):
        if not self.window or view == self.view:
            return

        self.view = view
        if view == SETTINGS:
            self.status_panel.pack_forget()
            self.settings_view.pack(fill="both", expand=True)
        else:
            self.settings_view.pack_forget()
            self.status_panel.pack(fill="both", expand=True)
            self.status_panel.sync_mode()

    def _on_history_window(self, seconds):
        self.history_window = seconds
        if self.status_panel:
            self.status_panel.set_history_window(seconds)

    def _on_minimize(self, event):
        if self.window and self.window.state() == "iconic":
            self.window.after(10, self._hide_window)

    def _load_window_position(self):
        if not self.window:
            return
        try:
            if WINDOW_STATE_FILE.exists():
                state = json.loads(WINDOW_STATE_FILE.read_text(encoding="utf-8"))
                self.window.geometry(f"+{state.get('window_x', 100)}+{state.get('window_y', 100)}")
        except (OSError, ValueError, TclError) as e:
            # TclError: a corrupted file could hold a geometry string Tk refuses.
            logger.debug(f"Could not load window position: {e}")

    def _save_window_position(self):
        if not self.window:
            return
        try:
            parts = self.window.geometry().split("+")
            if len(parts) != 3:
                return

            WINDOW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {"window_x": int(parts[1]), "window_y": int(parts[2])}
            WINDOW_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except (OSError, ValueError, TclError) as e:
            logger.debug(f"Could not save window position: {e}")

    def _show_window(self, icon=None, item=None):
        if self.window:
            self.window.after(0, self.window.deiconify)
            self.window.after(0, self.window.state, "normal")
            self.window.after(0, self.window.lift)
            self.window.after(0, self.window.focus_force)

    def _hide_window(self):
        if self.window:
            self._save_window_position()
            self.window.withdraw()

    # --- control --------------------------------------------------------------------------

    def _start_control(self, icon=None, item=None):
        if self.loop and not self.controller.running:
            asyncio.run_coroutine_threadsafe(self.controller.start(), self.loop)

    def _stop_control(self, icon=None, item=None):
        if self.loop and self.controller.running:
            asyncio.run_coroutine_threadsafe(self.controller.stop(), self.loop)

    def _exit_app(self, icon=None, item=None):
        self._running = False
        if self.status_panel:
            # Stop the queue poller first, or its next `after` fires on a destroyed widget.
            self.status_panel.stop_polling()

        if self.loop and self.controller.running:
            future = asyncio.run_coroutine_threadsafe(self.controller.stop(), self.loop)
            try:
                future.result(timeout=2.0)
            except Exception as e:  # noqa: BLE001 -- surfaces whatever controller.stop() raised, on shutdown
                logger.debug(f"Error stopping controller: {e}")

        if self.window:
            self._save_window_position()
            self.window.after(0, self.window.destroy)

        if self.tray_icon:
            self.tray_icon.stop()

        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _run_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self.controller.start())
        self.loop.run_forever()

    # --- lifecycle ------------------------------------------------------------------------

    def run(self):
        self._running = True
        self.tray_icon = self._create_tray_icon()
        self._create_window()

        threading.Thread(target=self._run_async_loop, daemon=True).start()

        def update_tooltip():
            if self._running and self.window:
                self._update_tray_tooltip()
                self.window.after(1000, update_tooltip)

        if self.window:
            self.window.after(1000, update_tooltip)

        threading.Thread(target=self.tray_icon.run_detached, daemon=True).start()

        if self.window:
            self.window.iconify()
            self.window.mainloop()

        self._running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
