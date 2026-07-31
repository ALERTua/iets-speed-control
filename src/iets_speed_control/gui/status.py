"""The Home destination: connection state, the three live numbers, temperature history, mode switch.

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
from .history import TemperatureHistory
from .theme import CARD, CPU_COLOR, FAN_COLOR, GPU_COLOR, HISTORY_WINDOW_SECONDS, MUTED, UI_POLL_MS

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
        for name, colour in (("CPU", CPU_COLOR), ("GPU", GPU_COLOR), ("Fan", FAN_COLOR)):
            cell = ctk.CTkFrame(numbers, fg_color="transparent")
            cell.pack(side="left", expand=True, pady=10)
            ctk.CTkLabel(cell, text=name, font=("", 10), text_color=MUTED).pack()
            value = ctk.CTkLabel(cell, text="--", font=("", 20, "bold"), text_color=colour)
            value.pack()
            self.value_labels[name] = value

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

        # Every reading matters for the graph, but only the last one for the labels.
        for cpu, gpu in temps:
            self.history.add(cpu, gpu)

        if "temps" in latest:
            cpu, gpu = latest["temps"]
            self.value_labels["CPU"].configure(text=f"{cpu} °C")
            self.value_labels["GPU"].configure(text=f"{gpu} °C")
        if "speed" in latest:
            self.value_labels["Fan"].configure(text=f"{latest['speed'][0]} %")
        if "status" in latest:
            self._apply_status(*latest["status"])

        if self._polling:
            self.after(UI_POLL_MS, self._drain)

    def stop_polling(self):
        self._polling = False

    # --- widget updates -------------------------------------------------------------------

    def _apply_status(self, connected: bool, running: bool):
        if connected:
            text = f"● Connected  {self.controller.port or ''}".rstrip()
            colour = CPU_COLOR
        else:
            text = "● Disconnected"
            colour = DISCONNECTED_COLOR
        if not running:
            text += "  (stopped)"
        self.status_label.configure(text=text, text_color=colour)

        if self.gui_app:
            self.gui_app.update_tray_icon(connected)

    def _on_mode_change(self, value):
        mode = Mode.AUTO if value == "Auto" else Mode.MANUAL
        self.controller.mode = mode
        if mode == Mode.MANUAL:
            # Hold whatever the fan is doing now rather than jumping to a stale manual value.
            self.controller.manual_speed = self.controller.current_speed

    def sync_mode(self):
        """Reflect a mode change made elsewhere (settings window, tray)."""
        self.mode_switch.set("Auto" if self.controller.mode == Mode.AUTO else "Manual")

    def set_history_window(self, seconds):
        self.history.set_window(seconds)
