"""The filter list of the Sensors card: one line per filter, with what each one catches right now.

Each line is a combo box. Typing gives a regular expression; picking from the dropdown gives the exact
name of a sensor the source reports now, escaped so that its brackets or dots match literally. Beside
it the line shows the hottest reading the filter catches, and the line holding the overall maximum, the
number the curve is evaluated at, is marked.
"""

import re
from collections.abc import Callable, Sequence

import customtkinter as ctk

from ..util.filters import Selection
from .theme import ERROR_COLOR, MAX_COLOR, MUTED

PATTERN_WIDTH = 260
NO_MATCH = "matches nothing"


class FilterLine(ctk.CTkFrame):
    """One filter: its pattern, its current match, and a button to remove it."""

    def __init__(self, master, pattern: str, suggestions: Sequence[str], on_commit, on_remove):
        super().__init__(master, fg_color="transparent")
        self._on_commit = on_commit

        self.combo = ctk.CTkComboBox(self, values=list(suggestions), width=PATTERN_WIDTH, command=self._picked)
        self.combo.pack(side="left")
        self.combo.set(pattern)
        self.combo.bind("<Return>", lambda _event: self._on_commit())
        self.combo.bind("<FocusOut>", lambda _event: self._on_commit())

        self.remove_button = ctk.CTkButton(
            self, text="✕", width=28, fg_color="transparent", border_width=1, command=lambda: on_remove(self)
        )
        self.remove_button.pack(side="right")

        self.result = ctk.CTkLabel(self, text="", font=("", 11), text_color=MUTED, anchor="w")
        self.result.pack(side="left", fill="x", expand=True, padx=(10, 6))

    @property
    def pattern(self) -> str:
        return self.combo.get().strip()

    def _picked(self, label: str):
        # A sensor picked from the list is meant literally: "CPU Core #1 (Tctl)" must not become a group.
        self.combo.set(re.escape(label))
        self._on_commit()

    def show(self, value: float | None, label: str | None, hottest: bool):
        if value is None:
            self.result.configure(text=NO_MATCH, text_color=ERROR_COLOR, font=("", 11))
            return

        marker = "▶ max  " if hottest else ""
        self.result.configure(
            text=f"{marker}{value:g} °C  ·  {label}",
            text_color=MAX_COLOR if hottest else MUTED,
            font=("", 11, "bold") if hottest else ("", 11),
        )


class FilterList(ctk.CTkFrame):
    """The editable list. `on_change` gets the new patterns and returns an error to show, or None."""

    def __init__(self, master, on_change: Callable[[list[str]], str | None], on_error: Callable[[str | None], None]):
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self._on_error = on_error
        self.lines: list[FilterLine] = []
        self.suggestions: list[str] = []
        self._committed: list[str] = []

    # --- content ----------------------------------------------------------------------------------

    @property
    def patterns(self) -> list[str]:
        return [line.pattern for line in self.lines if line.pattern]

    def set_patterns(self, patterns: Sequence[str]):
        """Show a list that is already committed, for example after the source changed."""
        for line in self.lines:
            line.destroy()
        self.lines = []
        for pattern in patterns:
            self._add_line(pattern)
        self._committed = list(patterns)

    def set_suggestions(self, labels: Sequence[str]):
        self.suggestions = sorted(labels)
        for line in self.lines:
            line.combo.configure(values=self.suggestions)

    def show_selection(self, selection: Selection):
        """Mark each line with its current match, and the line that holds the maximum."""
        by_pattern = {match.pattern: match for match in selection.matches}
        for line in self.lines:
            match = by_pattern.get(line.pattern)
            line.show(
                match.value if match else None,
                match.label if match else None,
                hottest=selection.hottest is not None and match is selection.hottest,
            )

    # --- editing ----------------------------------------------------------------------------------

    def add_filter(self):
        """Add an empty line for the user to type into or pick for; it counts once it has text."""
        line = self._add_line("")
        line.combo.focus_set()

    def _add_line(self, pattern: str) -> FilterLine:
        line = FilterLine(self, pattern, self.suggestions, on_commit=self.commit, on_remove=self._remove)
        line.pack(fill="x", pady=2)
        self.lines.append(line)
        return line

    def _remove(self, line: FilterLine):
        if line.pattern and len(self.patterns) == 1:
            # The last filter cannot go: with none, every reading is ignored and the fan idles at the floor.
            self._on_error("Keep at least one filter")
            return

        self.lines.remove(line)
        line.destroy()
        self.commit()

    def commit(self):
        patterns = self.patterns
        if patterns == self._committed:
            self._on_error(None)
            return

        error = self._on_change(patterns)
        self._on_error(error)
        if error is None:
            self._committed = patterns
