"""Home's list of the tracked sensors: what each one reads now, and which one gives the maximum.

The readings arrive every tick, about once a second. Building and destroying labels each time would churn
Tk for nothing, so the rows are reused and only their text and colour change; a row is added or removed
only when the number of tracked sensors changes.
"""

from collections.abc import Mapping

import customtkinter as ctk

from ..sensors import PROVIDER_LABELS
from ..util.tracking import Selection, SensorReading, SourceState
from .theme import ERROR_COLOR, MAX_COLOR, MUTED

NO_READING = "no reading"
ASLEEP = "asleep"
NOT_MONITORING = "Monitoring stopped: no sensors tracked"
MAX_MARKER = "▶"


def source_label(name: str) -> str:
    """The product name a person knows the source by; the code name only for a source this build lacks."""
    return PROVIDER_LABELS.get(name, name)


class TrackedReadings(ctk.CTkFrame):
    """One row per tracked sensor: its source, its own label and its value."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.columnconfigure(1, weight=1)
        self.rows: list[tuple[ctk.CTkLabel, ctk.CTkLabel, ctk.CTkLabel]] = []
        self.message = ctk.CTkLabel(self, text="", text_color=MUTED, anchor="w")

    def show(self, selection: Selection, states: Mapping[str, SourceState] | None = None):
        """Show every tracked sensor of `selection`; `states` tells a sleeping source from a silent one."""
        states = states or {}
        self._hide_message()
        self._resize(len(selection.readings))
        for row, reading in zip(self.rows, selection.readings, strict=True):
            self._show_row(row, reading, reading is selection.hottest, states.get(reading.source))

    def show_stopped(self):
        """Nothing is tracked: say so instead of an empty card, since it is a choice rather than a fault."""
        self._resize(0)
        self.message.configure(text=NOT_MONITORING)
        if not self.message.winfo_manager():
            self.message.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=1)

    def _hide_message(self):
        if self.message.winfo_manager():
            self.message.grid_remove()

    @staticmethod
    def _show_row(row, reading: SensorReading, hottest: bool, state: SourceState | None):
        source, sensor, value = row
        colour = MAX_COLOR if hottest else MUTED
        font = ("", 12, "bold") if hottest else ("", 12)
        name = source_label(reading.source)
        source.configure(text=f"{MAX_MARKER} {name}" if hottest else name, text_color=colour, font=font)
        sensor.configure(text=reading.label, text_color=colour, font=font)
        if reading.value is not None:
            value.configure(text=f"{reading.value:g} °C", text_color=colour, font=font)
        elif state is SourceState.ASLEEP:
            # A GPU powered off to save energy is cold, not broken: no alarm colour.
            value.configure(text=ASLEEP, text_color=MUTED, font=("", 12))
        else:
            value.configure(text=NO_READING, text_color=ERROR_COLOR, font=("", 12))

    def _resize(self, count: int):
        for row in self.rows[count:]:
            for label in row:
                label.destroy()
        del self.rows[count:]
        # Below the message's row, so that showing the message never has to move a reading.
        for index in range(len(self.rows), count):
            row = (
                ctk.CTkLabel(self, text="", anchor="w"),
                ctk.CTkLabel(self, text="", anchor="w"),
                ctk.CTkLabel(self, text="", anchor="e", width=90),
            )
            row[0].grid(row=index + 1, column=0, sticky="w", padx=(12, 8), pady=1)
            row[1].grid(row=index + 1, column=1, sticky="w", padx=8, pady=1)
            row[2].grid(row=index + 1, column=2, sticky="e", padx=(8, 12), pady=1)
            self.rows.append(row)
