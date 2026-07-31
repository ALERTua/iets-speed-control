"""Settings view: grouped cards and search.

One of the two destinations in the main window; navigation between sections lives in the left rail,
so this view only owns the content.

Changes apply immediately -- there is no OK/Apply/Cancel. Per the GNOME HIG, an explicit apply
button is only warranted when applying takes longer than about a second, and every setting here is
in-memory. Writing settings to disk is a separate action and arrives with the YAML config work.
"""

import logging
from collections.abc import Callable

import customtkinter as ctk

from ..controller import Mode, SpeedController
from ..util import env
from .curve_editor import CurveEditor
from .theme import (
    BACKGROUND,
    CARD,
    HISTORY_WINDOW_CHOICES,
    HISTORY_WINDOW_SECONDS,
    MUTED,
    ROW_LINE,
)

logger = logging.getLogger(__name__)


def format_span(seconds: int) -> str:
    minutes = seconds / 60
    if minutes < 1:
        return f"{seconds} seconds"

    return f"{minutes:g} minute" if minutes == 1 else f"{minutes:g} minutes"


SPAN_LABELS = {format_span(seconds): seconds for seconds in HISTORY_WINDOW_CHOICES}


class SettingsRow:
    """One row inside a card: title and description on the left, control on the right."""

    def __init__(self, card, title, subtitle, control, row_index):
        self.title = title
        self.subtitle = subtitle
        self.control = control
        self.separator = None

        if row_index:
            self.separator = ctk.CTkFrame(card, height=1, fg_color=ROW_LINE)
            self.separator.grid(row=row_index * 2 - 1, column=0, columnspan=2, sticky="ew", padx=14)

        self.text = ctk.CTkFrame(card, fg_color="transparent")
        self.text.grid(row=row_index * 2, column=0, sticky="w", padx=14, pady=9)
        ctk.CTkLabel(self.text, text=title, font=("", 13), anchor="w").pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(self.text, text=subtitle, font=("", 10), text_color=MUTED, anchor="w").pack(anchor="w")

        control.grid(row=row_index * 2, column=1, sticky="e", padx=14, pady=9)
        card.columnconfigure(0, weight=1)

    def matches(self, needle: str) -> bool:
        return needle in self.title.lower() or needle in self.subtitle.lower()


class SettingsSection:
    """A named group of rows, rendered as a titled card."""

    def __init__(self, parent, title):
        self.title = title
        self.rows: list[SettingsRow] = []
        self.holder = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(self.holder, text=title.upper(), font=("", 11, "bold"), text_color=MUTED, anchor="w").pack(
            anchor="w", padx=4, pady=(12, 4)
        )
        self.card = ctk.CTkFrame(self.holder, fg_color=CARD, corner_radius=8)
        self.card.pack(fill="both", expand=True)

    def add_row(self, title, subtitle, control) -> SettingsRow:
        row = SettingsRow(self.card, title, subtitle, control, len(self.rows))
        self.rows.append(row)
        return row

    def add_widget(self, widget):
        """Attach a full-width widget (the curve editor) under the rows."""
        widget.grid(row=len(self.rows) * 2, column=0, columnspan=2, sticky="nsew", padx=14, pady=(4, 12))
        self.card.rowconfigure(len(self.rows) * 2, weight=1)

    def matches(self, needle: str) -> bool:
        return needle in self.title.lower() or any(row.matches(needle) for row in self.rows)


class SettingsView(ctk.CTkFrame):
    """The settings surface. Lives inside the main window; the shell shows and hides it."""

    SECTION_NAMES = ("Curve", "Manual", "Display")

    def __init__(
        self,
        master,
        controller: SpeedController,
        on_history_window: Callable[[int], None] | None = None,
        history_window: int = HISTORY_WINDOW_SECONDS,
    ):
        super().__init__(master, fg_color=BACKGROUND)
        self.controller = controller
        self.on_history_window = on_history_window
        self.history_window = history_window

        self.sections: dict[str, SettingsSection] = {}
        self.active: str | None = None

        self._build_body()
        self._build_curve_section()
        self._build_manual_section()
        self._build_display_section()
        self.show(self.SECTION_NAMES[0])

    # --- shell ----------------------------------------------------------------------------

    def _build_body(self):
        search_bar = ctk.CTkFrame(self, fg_color="transparent")
        search_bar.pack(fill="x", padx=18, pady=(16, 0))
        self.search = ctk.CTkEntry(search_bar, placeholder_text="Search settings…", height=34)
        self.search.pack(fill="x")
        self.search.bind("<KeyRelease>", lambda _event: self.filter())

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=12, pady=8)

    def _add_section(self, title) -> SettingsSection:
        section = SettingsSection(self.body, title)
        self.sections[title] = section
        return section

    def show(self, name):
        """Show one section. Navigation lives in the rail, so this only swaps the content."""
        # Only clear when there is text: CTkEntry drops its placeholder on an empty delete().
        if self.search.get():
            self.search.delete(0, "end")
        for section in self.sections.values():
            section.holder.pack_forget()

        self.sections[name].holder.pack(fill="both", expand=True)
        self.active = name

    def filter(self):
        needle = self.search.get().strip().lower()
        if not needle:
            self.show(self.active or next(iter(self.sections)))
            return

        for section in self.sections.values():
            section.holder.pack_forget()
        for section in self.sections.values():
            if section.matches(needle):
                section.holder.pack(fill="x")

    # --- Curve ----------------------------------------------------------------------------

    def _build_curve_section(self):
        section = self._add_section("Curve")

        selected = ctk.CTkFrame(section.card, fg_color="transparent")
        section.add_row("Selected point", "Drag on the graph, or type exact values and press Enter", selected)

        ctk.CTkLabel(selected, text="°C", font=("", 11), text_color=MUTED).pack(side="left", padx=(0, 4))
        self.point_temp = ctk.CTkEntry(selected, width=70)
        self.point_temp.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(selected, text="%", font=("", 11), text_color=MUTED).pack(side="left", padx=(0, 4))
        self.point_pct = ctk.CTkEntry(selected, width=70)
        self.point_pct.pack(side="left")
        for entry in (self.point_temp, self.point_pct):
            entry.bind("<Return>", lambda _event: self._apply_point_entries())

        section.add_row(
            "Reset curve",
            "Back to the curve the app started with",
            ctk.CTkButton(section.card, text="Reset", width=110, command=self._reset_curve),
        )

        self.editor = CurveEditor(section.card, points=self.controller.curve, on_change=self._on_curve_change)
        section.add_widget(self.editor)

        hint = ctk.CTkLabel(
            section.card,
            text="drag to move · double-click to add · right-click to remove",
            font=("", 10),
            text_color=MUTED,
        )
        hint.grid(row=len(section.rows) * 2 + 1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 10))

        self._initial_curve = self.controller.curve
        self.editor.redraw()

    def _on_curve_change(self, points, selected):
        temp, pct = points[selected]
        for entry, value in ((self.point_temp, f"{temp:g}"), (self.point_pct, f"{pct:g}")):
            if entry.get() != value:
                entry.delete(0, "end")
                entry.insert(0, value)

        try:
            self.controller.curve = points
        except ValueError as e:
            logger.warning(f"Rejected curve: {e}")

    def _apply_point_entries(self):
        try:
            temp = float(self.point_temp.get().replace(",", "."))
            pct = float(self.point_pct.get().replace(",", "."))
        except ValueError:
            logger.debug("Ignoring non-numeric point entry")
            self.editor.redraw()
            return

        self.editor.update_selected(temp=temp, pct=pct)

    def _reset_curve(self):
        self.editor.set_points(self._initial_curve)

    # --- Manual speed ---------------------------------------------------------------------

    def _build_manual_section(self):
        section = self._add_section("Manual")

        self.mode_switch = ctk.CTkSegmentedButton(section.card, values=["Auto", "Manual"], command=self._on_mode_change)
        self.mode_switch.set("Auto" if self.controller.mode == Mode.AUTO else "Manual")
        section.add_row("Control mode", "Auto follows the curve; Manual holds a fixed speed", self.mode_switch)

        self.manual_value = ctk.CTkLabel(section.card, text=f"{self.controller.manual_speed} %", width=48)
        self.manual_slider = ctk.CTkSlider(
            section.card, from_=0, to=100, number_of_steps=100, width=220, command=self._on_manual_change
        )
        self.manual_slider.set(self.controller.manual_speed)

        holder = ctk.CTkFrame(section.card, fg_color="transparent")
        section.add_row("Manual speed", "Used only in Manual mode", holder)
        self.manual_slider.pack(in_=holder, side="left")
        self.manual_value.pack(in_=holder, side="left", padx=(10, 0))

        section.add_row(
            "Sensor filters",
            f"CPU matches “{env.CPU_SENSOR_FILTER}”, GPU matches “{env.GPU_SENSOR_FILTER}”."
            " Editable once settings move to the config file.",
            ctk.CTkLabel(section.card, text=env.SENSOR_PROVIDER, font=("", 12), text_color=MUTED),
        )

    def _on_mode_change(self, value):
        self.controller.mode = Mode.AUTO if value == "Auto" else Mode.MANUAL

    def _on_manual_change(self, value):
        speed = int(value)
        self.manual_value.configure(text=f"{speed} %")
        self.controller.manual_speed = speed

    # --- Display --------------------------------------------------------------------------

    def _build_display_section(self):
        section = self._add_section("Display")

        self.span_menu = ctk.CTkOptionMenu(
            section.card, values=list(SPAN_LABELS), width=200, command=self._on_span_change
        )
        self.span_menu.set(format_span(self.history_window))
        section.add_row(
            "Graph timeline",
            "How far back the temperature history on the main window reaches",
            self.span_menu,
        )

    def _on_span_change(self, label):
        seconds = SPAN_LABELS[label]
        self.history_window = seconds
        if self.on_history_window:
            self.on_history_window(seconds)
        logger.debug(f"Graph timeline set to {seconds} s")
