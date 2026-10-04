"""The Home destination: connection state, the maximum and the fan, each filter's reading, history, mode.

Speed control and the fan curve live in the Settings destination; navigation between the two is the
rail's job, so nothing here opens another view.

Threading: the controller runs its loop on the asyncio thread and calls its callbacks from there.
Tk is not thread-safe -- touching a widget from that thread crashes the process with an access
violation, which is exactly what drawing the history graph used to do. So the callbacks only put
plain data on a queue, and the Tk thread drains it from a periodic `after` job.
"""

import logging
import queue

import customtkinter as ctk

from ..controller import Mode, SpeedController
from ..sensors import lacks_admin_rights
from .filter_readings import FilterReadings
from .history import TemperatureHistory
from .theme import ACCENT_COLOR, CARD, FAN_COLOR, HISTORY_WINDOW_SECONDS, MAX_COLOR, MUTED, UI_POLL_MS

logger = logging.getLogger(__name__)

DISCONNECTED_COLOR = "#d05050"


class StatusPanel(ctk.CTkFrame):
    """Read-only status plus the mode switch."""

    def __init__(self, master, controller: SpeedController, gui_app=None):
        super().__init__(master, fg_color="transparent")
        self.controller = controller
        self.gui_app = gui_app

        self._updates: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self._polling = True

        self._build_ui()
        self.controller.set_callbacks(
            on_status_change=lambda *args: self._post("status", args),
            on_temps_change=lambda *args: self._post("temps", args),
            on_speed_change=lambda *args: self._post("speed", args),
        )
        self.after(UI_POLL_MS, self._drain)

    def _build_ui(self):
        # No settings button here: navigation belongs to the rail, and two ways in is one too many.
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(14, 4))
        self.status_label = ctk.CTkLabel(top, text="● Disconnected", font=("", 12), text_color=DISCONNECTED_COLOR)
        self.status_label.pack(side="left")

        numbers = ctk.CTkFrame(self, fg_color=CARD, corner_radius=8)
        numbers.pack(fill="x", padx=14, pady=6)
        self.value_labels = {}
        for name, colour in (("Max", MAX_COLOR), ("Fan", FAN_COLOR)):
            cell = ctk.CTkFrame(numbers, fg_color="transparent")
            cell.pack(side="left", expand=True, pady=10)
            ctk.CTkLabel(cell, text=name, font=("", 10), text_color=MUTED).pack()
            value = ctk.CTkLabel(cell, text="--", font=("", 20, "bold"), text_color=colour)
            value.pack()
            self.value_labels[name] = value

        # Every filter of the active source with its reading; the one that gives the maximum is marked.
        self.filter_readings = FilterReadings(self, fg_color=CARD, corner_radius=8)
        self.filter_readings.pack(fill="x", padx=14, pady=(0, 6), ipady=4)

        # The graph takes whatever room is left: a ten-minute span needs the width and height.
        self.history = TemperatureHistory(self, window_seconds=HISTORY_WINDOW_SECONDS, height=220)
        self.history.pack(fill="both", expand=True, padx=14, pady=4)

        self.mode_switch = ctk.CTkSegmentedButton(self, values=["Auto", "Manual"], command=self._on_mode_change)
        self.mode_switch.set("Auto" if self.controller.mode == Mode.AUTO else "Manual")
        self.mode_switch.pack(fill="x", padx=14, pady=(8, 14))

    # --- cross-thread plumbing ------------------------------------------------------------

    def _post(self, kind, args):
        """Called from the asyncio thread. Must not touch any widget."""
        self._updates.put((kind, args))

    def _drain(self):
        """Runs on the Tk thread. Applies the latest update of each kind and reschedules itself."""
        latest: dict[str, tuple] = {}
        temps: list[tuple] = []
        try:
            while True:
                kind, args = self._updates.get_nowait()
                latest[kind] = args
                if kind == "temps":
                    temps.append(args)
        except queue.Empty:
            pass

        # Every reading matters for the graph, but only the last one for the labels -- and one redraw
        # is enough for the whole batch, however many readings the drain picked up.
        for max_temp, _selection in temps:
            self.history.add(max_temp)
        if temps:
            self.history.redraw()

        if "temps" in latest:
            max_temp, selection = latest["temps"]
            self.value_labels["Max"].configure(text=f"{max_temp} °C")
            self.filter_readings.show(selection)
        if "speed" in latest:
            self.value_labels["Fan"].configure(text=f"{latest['speed'][0]} %")
        if "status" in latest:
            self._apply_status(*latest["status"])

        if self._polling:
            self.after(UI_POLL_MS, self._drain)

    def stop_polling(self):
        self._polling = False

    # --- widget updates -------------------------------------------------------------------

    def _apply_status(self, connected: bool, running: bool, sensors_ok: bool = True, loop_ok: bool = True):
        """Two things can be wrong, and the difference matters when you are fixing it.

        A dead serial link leaves the fan wherever it was; a silent temperature source is worse,
        because the loop keeps running and drives the fan from 0 °C -- the curve's floor.
        """
        if not connected:
            text = "● Disconnected"
            colour = DISCONNECTED_COLOR
        elif not sensors_ok:
            source = getattr(self.controller.sensors, "name", "the sensor source")
            text = f"● No temperatures from {source}"
            if lacks_admin_rights(self.controller.sensors):
                text += ": restart as administrator"
            colour = DISCONNECTED_COLOR
        elif not loop_ok:
            # The link and the source work, but ticks raise: the fan is not being driven.
            text = "● Control loop error: see the log"
            colour = DISCONNECTED_COLOR
        else:
            text = f"● Connected  {self.controller.port or ''}".rstrip()
            colour = ACCENT_COLOR

        if not running:
            text += "  (stopped)"
        self.status_label.configure(text=text, text_color=colour)

        if self.gui_app:
            self.gui_app.update_tray_icon(connected and sensors_ok and loop_ok)

    def _on_mode_change(self, value):
        # The manual speed is whatever the user last set, kept in the configuration between runs.
        # Seeding it from the current automatic speed here would overwrite that choice.
        self.controller.mode = Mode.AUTO if value == "Auto" else Mode.MANUAL

    def sync_mode(self):
        """Reflect a mode change made elsewhere (settings window, tray)."""
        self.mode_switch.set("Auto" if self.controller.mode == Mode.AUTO else "Manual")

    def set_history_window(self, seconds):
        self.history.set_window(seconds)
