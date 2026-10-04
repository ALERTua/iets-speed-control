"""Home's list of the active filters: what each one reads now, and which one gives the maximum.

The readings arrive every tick, about once a second. Rebuilding the labels each time would churn Tk
for nothing, so the rows are rebuilt only when the list of filters itself changes, and otherwise only
their text and colour are updated.
"""

import customtkinter as ctk

from ..util.filters import Selection
from .theme import ERROR_COLOR, MAX_COLOR, MUTED

NO_MATCH = "matches nothing"


class FilterReadings(ctk.CTkFrame):
    """One row per filter: the pattern, its hottest reading, and the sensor it came from."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.columnconfigure(2, weight=1)
        self.rows: list[tuple[ctk.CTkLabel, ctk.CTkLabel, ctk.CTkLabel]] = []
        self._patterns: tuple[str, ...] = ()

    def show(self, selection: Selection):
        patterns = tuple(match.pattern for match in selection.matches)
        if patterns != self._patterns:
            self._rebuild(patterns)

        for (pattern, value, sensor), match in zip(self.rows, selection.matches, strict=True):
            hottest = selection.hottest is not None and match is selection.hottest
            colour = MAX_COLOR if hottest else MUTED
            font = ("", 12, "bold") if hottest else ("", 12)
            pattern.configure(text=f"▶ {match.pattern}" if hottest else match.pattern, text_color=colour, font=font)
            if match.value is None:
                value.configure(text="—", text_color=ERROR_COLOR, font=font)
                sensor.configure(text=NO_MATCH, text_color=ERROR_COLOR)
            else:
                value.configure(text=f"{match.value:g} °C", text_color=colour, font=font)
                sensor.configure(text=match.label, text_color=colour)

    def _rebuild(self, patterns: tuple[str, ...]):
        for row in self.rows:
            for label in row:
                label.destroy()
        self.rows = []
        for index, _pattern in enumerate(patterns):
            row = (
                ctk.CTkLabel(self, text="", anchor="w"),
                ctk.CTkLabel(self, text="", anchor="e", width=70),
                ctk.CTkLabel(self, text="", anchor="w", font=("", 11)),
            )
            row[0].grid(row=index, column=0, sticky="w", padx=(12, 8), pady=1)
            row[1].grid(row=index, column=1, sticky="e", padx=8, pady=1)
            row[2].grid(row=index, column=2, sticky="w", padx=(8, 12), pady=1)
            self.rows.append(row)
        self._patterns = patterns
