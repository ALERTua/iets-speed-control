"""Tray application: window, tray icon and the asyncio thread the controller runs on."""

import asyncio
import logging
import re
import threading
from pathlib import Path
from tkinter import TclError

import customtkinter as ctk
import pystray
from PIL import Image
from pystray import MenuItem as Item

from ..controller import SpeedController
from ..util.config import CONFIG
from ..util.config import save as save_config
from .nav import NavigationRail, NavItem
from .settings import SettingsView
from .status import StatusPanel
from .theme import APP_NAME, BACKGROUND, WINDOW_MIN_SIZE, WINDOW_SIZE

HOME = "home"
SETTINGS = "settings"

NAV_ITEMS = [
    NavItem(HOME, "Home", "⌂"),
    NavItem(SETTINGS, "Settings", "⚙"),
    *[NavItem(f"settings:{name}", name, parent=SETTINGS) for name in SettingsView.SECTION_NAMES],
]

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).parent.parent.parent.parent / "media"

# "980x680+80+80", and "980x680-1900+40" for a monitor left of the primary one. Splitting on "+"
# loses that second case, which is why this is parsed rather than split.
GEOMETRY = re.compile(r"^(?P<width>\d+)x(?P<height>\d+)(?P<x>[+-]\d+)(?P<y>[+-]\d+)$")

# How much of the window has to be on the desktop for it to be usable: enough of the title bar to
# grab with the mouse. A saved position can end up outside it after a monitor is unplugged or the
# displays are rearranged, and a window restored out there cannot be reached at all.
VISIBLE_MARGIN = 80

# SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
VIRTUAL_SCREEN_METRICS = (76, 77, 78, 79)


def desktop_bounds() -> tuple[int, int, int, int] | None:
    """Bounding box of every monitor as (left, top, right, bottom), or None if unavailable.

    Tk's winfo_screenwidth reports the primary monitor only, so a window parked on a second screen
    would look off-desktop. The virtual-screen metrics cover the whole arrangement.
    """
    try:
        import ctypes

        metric = ctypes.windll.user32.GetSystemMetrics  # type: ignore[attr-defined]
        left, top, width, height = (metric(index) for index in VIRTUAL_SCREEN_METRICS)
    except (AttributeError, OSError) as e:
        logger.debug(f"Could not read the virtual screen metrics: {e}")
        return None

    if width <= 0 or height <= 0:
        return None

    return left, top, left + width, top + height


def is_on_desktop(x: int, y: int, width: int, height: int, bounds: tuple[int, int, int, int]) -> bool:
    """Whether a window at this position could be seen and dragged.

    The top edge has to be on the desktop, otherwise the title bar sits above it and the window
    cannot be moved back; and enough of the width has to overlap to be visible at all.
    """
    left, top, right, bottom = bounds
    overlap = min(x + width, right) - max(x, left)
    return overlap >= VISIBLE_MARGIN and top <= y <= bottom - VISIBLE_MARGIN


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
        self.history_window = CONFIG.ui.history_window

        self.icon_path = MEDIA_DIR / "icon.ico"
        self.icon_image = self._load_icon(self.icon_path)
        self.icon_red_image = self._load_icon(MEDIA_DIR / "icon-red.ico")
        self._is_healthy = True

    # --- icons ----------------------------------------------------------------------------

    def _load_icon(self, icon_path: Path) -> Image.Image | None:
        try:
            if icon_path.exists():
                return Image.open(icon_path)
            return Image.new("RGB", (64, 64), color="blue")
        except (OSError, ValueError) as e:
            logger.error(f"Failed to load icon: {e}")
            return Image.new("RGB", (64, 64), color="blue")

    def update_tray_icon(self, healthy: bool):
        """Red whenever the app cannot do its job: no device, or no temperatures to act on."""
        if healthy == self._is_healthy:
            return

        self._is_healthy = healthy
        if self.tray_icon:
            self.tray_icon.icon = self.icon_image if healthy else self.icon_red_image

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

        self.rail = NavigationRail(
            self.window,
            NAV_ITEMS,
            on_select=self._on_nav_select,
            collapsed=CONFIG.ui.rail_collapsed,
            on_exit=self._exit_app,
        )
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
            on_reconnect=self._reconnect_device,
        )
        self.status_panel.pack(fill="both", expand=True)
        self.rail.select(HOME, notify=False)

        self.window.protocol("WM_DELETE_WINDOW", self._exit_app)
        self.window.bind("<Unmap>", self._on_minimize)
        self._load_window_geometry()

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
            self.settings_view.sync_mode()
        else:
            self.settings_view.pack_forget()
            self.status_panel.pack(fill="both", expand=True)
            self.status_panel.sync_mode()

    def _on_history_window(self, seconds):
        self.history_window = seconds
        CONFIG.ui.history_window = seconds
        if self.status_panel:
            self.status_panel.set_history_window(seconds)

    def _on_minimize(self, event):
        if not CONFIG.ui.hide_to_tray_on_minimize:
            return  # leave it minimized in the taskbar, the way any other window behaves

        if self.window and self.window.state() == "iconic":
            self.window.after(10, self._hide_window)

    def _load_window_geometry(self):
        """Restore where and how big the window was, from the same config file as everything else.

        Size and position go in one geometry call so the window is not laid out twice, and the sign
        is written explicitly: a window on a monitor left of the primary one has a negative x.
        """
        if not self.window:
            return

        width = CONFIG.ui.window_width or WINDOW_SIZE[0]
        height = CONFIG.ui.window_height or WINDOW_SIZE[1]
        size = f"{width}x{height}" if CONFIG.ui.window_width and CONFIG.ui.window_height else ""

        place = ""
        if CONFIG.ui.window_x is not None and CONFIG.ui.window_y is not None:
            bounds = desktop_bounds()
            if bounds is None or is_on_desktop(CONFIG.ui.window_x, CONFIG.ui.window_y, width, height, bounds):
                place = f"{CONFIG.ui.window_x:+d}{CONFIG.ui.window_y:+d}"
            else:
                # Keep the size but let the window manager place it: the saved spot is on a screen
                # that is no longer there, and restoring it would put the window out of reach.
                logger.info(
                    f"Ignoring the saved window position {CONFIG.ui.window_x},{CONFIG.ui.window_y}:"
                    f" it is outside the desktop {bounds}"
                )

        if not size and not place:
            return

        try:
            self.window.geometry(size + place)
            # Realize the request now. run() iconifies the window immediately after this, and
            # Windows discards a position that Tk has not applied yet -- it cascades the window to
            # its own default spot instead. The size survives that, which is why only the position
            # appeared not to be restored.
            self.window.update_idletasks()
        except TclError as e:
            logger.debug(f"Could not restore the window geometry: {e}")

    def _save_window_geometry(self):
        if not self.window:
            return
        try:
            match = GEOMETRY.match(self.window.geometry())
            if not match:
                logger.debug(f"Unrecognised window geometry {self.window.geometry()!r}")
                return

            CONFIG.ui.window_width = int(match["width"])
            CONFIG.ui.window_height = int(match["height"])
            CONFIG.ui.window_x = int(match["x"])
            CONFIG.ui.window_y = int(match["y"])
            CONFIG.ui.rail_collapsed = bool(self.rail and self.rail.collapsed)
            save_config(CONFIG)
        except (OSError, ValueError, TclError) as e:
            logger.debug(f"Could not save the window geometry: {e}")

    def _show_window(self, icon=None, item=None):
        if self.window:
            self.window.after(0, self.window.deiconify)
            self.window.after(0, self.window.state, "normal")
            self.window.after(0, self.window.lift)
            self.window.after(0, self.window.focus_force)

    def _present_window(self):
        """Decide whether the window is shown at all on startup.

        Its own method rather than two lines inside run(), which blocks on mainloop and so cannot be
        exercised by a test.
        """
        if not self.window:
            return

        if CONFIG.ui.minimize_on_launch:
            self.window.iconify()
        else:
            self.window.deiconify()
            self.window.lift()

    def _hide_window(self):
        if self.window:
            self._save_window_geometry()
            self.window.withdraw()

    # --- control --------------------------------------------------------------------------

    def _start_control(self, icon=None, item=None):
        if self.loop and not self.controller.running:
            asyncio.run_coroutine_threadsafe(self.controller.start(), self.loop)

    def _stop_control(self, icon=None, item=None):
        if self.loop and self.controller.running:
            asyncio.run_coroutine_threadsafe(self.controller.stop(), self.loop)

    def _reconnect_device(self):
        """Reopen the serial port on the asyncio thread; the settings view polls the future."""
        if not self.loop:
            logger.debug("No event loop yet; cannot reconnect")
            return None

        return asyncio.run_coroutine_threadsafe(self.controller.reconnect(), self.loop)

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
            self._save_window_geometry()
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
            self._present_window()
            self.window.mainloop()

        self._running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
