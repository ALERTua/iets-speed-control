"""Settings view: grouped cards, search, and every key the configuration file has.

One of the two destinations in the main window; navigation between sections lives in the left rail,
so this view only owns the content.

Edits apply as soon as they validate. Per the GNOME HIG an explicit apply button is only warranted
when applying takes longer than about a second, and almost everything here is in-memory. The one
exception is `device.*`: port, baudrate, timeout and the command name are read when the serial port
is opened, so that section carries an explicit Reconnect button rather than a silent promise.

Writing to disk stays a separate action, in the footer, because the file is the user's own document.

Threading: reading sensors and reopening a serial port block for up to a second, and Tk is not
thread-safe. Those run on a worker thread that only puts its result on a queue; the Tk thread picks
it up from a short `after` poll.
"""

import logging
import os
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from tkinter import TclError

import customtkinter as ctk
from serial.tools.list_ports_windows import comports

from ..controller import Mode, SpeedController
from ..sensors import PROVIDER_LABELS, PROVIDER_NAMES, find_provider, lacks_admin_rights
from ..util.config import CONFIG, CONFIG_PATH, LOG_LEVELS, ConfigError, get_value, set_value, with_value
from ..util.config import defaults as config_defaults
from ..util.config import save as save_config
from ..util.logger import default_log_file
from ..util.logger import reconfigure as reapply_log_settings
from .curve_editor import CurveEditor
from .theme import (
    BACKGROUND,
    CARD,
    ERROR_COLOR,
    FIELD_WIDTH,
    HISTORY_WINDOW_CHOICES,
    HISTORY_WINDOW_SECONDS,
    MUTED,
    ROW_LINE,
    UI_POLL_MS,
)

logger = logging.getLogger(__name__)

BAUDRATES = ("9600", "19200", "38400", "57600", "115200")
LHM_WEB = "lhm-web"
ADMIN_NOTE = "Needs admin rights: restart as administrator"


def format_span(seconds: int) -> str:
    minutes = seconds / 60
    if minutes < 1:
        return f"{seconds} seconds"

    return f"{minutes:g} minute" if minutes == 1 else f"{minutes:g} minutes"


SPAN_LABELS = {format_span(seconds): seconds for seconds in HISTORY_WINDOW_CHOICES}


# --- value conversion ---------------------------------------------------------------------------
#
# Entries hand back text; the configuration holds typed values. A parser raises ValueError with a
# message meant for the user, and the row shows it instead of writing anything.


def as_text(value) -> str:
    return "" if value is None else str(value)


def parse_int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ValueError(f"{text.strip()!r} is not a whole number") from None


def parse_float(text: str) -> float:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{text.strip()!r} is not a number") from None


def parse_required(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("this cannot be empty")

    return stripped


def parse_optional(text: str) -> str | None:
    """Empty means "unset", which is how the configuration spells "use the default"."""
    return text.strip() or None


def parse_plain(text: str) -> str:
    """Kept as typed apart from whitespace; an empty string is a legitimate value (a password)."""
    return text.strip()


class SettingsRow:
    """One row inside a card: title, description and any error on the left, control on the right."""

    # The row builders attach the typed control under its own name, so callers need no cast. Only the
    # one matching the kind of row exists on a given instance.
    entry: ctk.CTkEntry
    menu: ctk.CTkOptionMenu
    combo: ctk.CTkComboBox
    switch: ctk.CTkSwitch

    def __init__(self, card, title, subtitle, control, row_index):
        self.title = title
        self.subtitle = subtitle
        self.control = control
        self.card = card
        self.separator = None
        self.error: ctk.CTkLabel | None = None

        if row_index:
            self.separator = ctk.CTkFrame(card, height=1, fg_color=ROW_LINE)
            self.separator.grid(row=row_index * 2 - 1, column=0, columnspan=2, sticky="ew", padx=14)

        self.text = ctk.CTkFrame(card, fg_color="transparent")
        self.text.grid(row=row_index * 2, column=0, sticky="ew", padx=14, pady=9)
        ctk.CTkLabel(self.text, text=title, font=("", 13), anchor="w").pack(anchor="w")

        # Descriptions are deliberately short rather than wrapped. Grid sizes the control column to
        # the widest control in the whole card, so the room left here is narrower than it looks, and
        # a description that does not fit is cut off mid-word. Wrapping is not a way out: CTkLabel
        # keeps its one-line height, so a second line is simply not drawn. test_gui_settings.py
        # asserts every description fits, which is the check that keeps this honest.
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = ctk.CTkLabel(self.text, text=subtitle, font=("", 10), text_color=MUTED, anchor="w")
            self.subtitle_label.pack(anchor="w")

        control.grid(row=row_index * 2, column=1, sticky="e", padx=14, pady=9)
        card.columnconfigure(0, weight=1)

    def matches(self, needle: str) -> bool:
        return needle in self.title.lower() or needle in self.subtitle.lower()

    def show_error(self, message: str):
        """Say why the value was refused, right under the description that asked for it."""
        if self.error is None:
            self.error = ctk.CTkLabel(self.text, text="", font=("", 10), text_color=ERROR_COLOR, anchor="w")
        self.error.configure(text=message)
        if not self.error.winfo_manager():
            self.error.pack(anchor="w", pady=(2, 0))

    def clear_error(self):
        if self.error is not None and self.error.winfo_manager():
            self.error.pack_forget()

    def set_visible(self, visible: bool):
        """Show or hide the whole row, keeping its place in the grid for when it comes back."""
        for widget in (self.separator, self.text, self.control):
            if widget is None:
                continue
            if visible:
                widget.grid()
            else:
                widget.grid_remove()


class SettingsSection:
    """A named group of rows, rendered as a titled card."""

    def __init__(self, parent, title):
        self.title = title
        self.rows: list[SettingsRow] = []
        # Which configuration keys this card owns, and how to redisplay each one. Reset needs both.
        self.bindings: list[tuple[str, Callable[[], None]]] = []
        self.on_show: Callable[[], None] | None = None
        self.after_reset: Callable[[], None] | None = None

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

    def register(self, path: str, refresh: Callable[[], None]):
        self.bindings.append((path, refresh))

    def matches(self, needle: str) -> bool:
        return needle in self.title.lower() or any(row.matches(needle) for row in self.rows)


class SettingsView(ctk.CTkFrame):
    """The settings surface. Lives inside the main window; the shell shows and hides it."""

    SECTION_NAMES = ("Curve", "Manual", "Sensors", "Device", "Control", "Logging", "Display")

    def __init__(
        self,
        master,
        controller: SpeedController,
        on_history_window: Callable[[int], None] | None = None,
        history_window: int = HISTORY_WINDOW_SECONDS,
        on_reconnect: Callable[[], Future | None] | None = None,
    ):
        super().__init__(master, fg_color=BACKGROUND)
        self.controller = controller
        self.on_history_window = on_history_window
        self.history_window = history_window
        # Reopening the port has to happen on the asyncio thread, which only the shell can reach.
        self.on_reconnect = on_reconnect

        self.sections: dict[str, SettingsSection] = {}
        self.active: str | None = None
        self.lhm_rows: list[SettingsRow] = []

        self._build_body()
        self._build_curve_section()
        self._build_manual_section()
        self._build_sensors_section()
        self._build_device_section()
        self._build_control_section()
        self._build_logging_section()
        self._build_display_section()
        self.show(self.SECTION_NAMES[0])

    # --- shell ----------------------------------------------------------------------------

    def _build_body(self):
        search_bar = ctk.CTkFrame(self, fg_color="transparent")
        search_bar.pack(fill="x", padx=18, pady=(16, 0))
        self.search = ctk.CTkEntry(search_bar, placeholder_text="Search settings…", height=34)
        self.search.pack(fill="x")
        self.search.bind("<KeyRelease>", lambda _event: self.filter())

        # The footer is packed before the body so it keeps its height when the body fills the rest.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
        self.save_button = ctk.CTkButton(footer, text="Save", width=110, command=self._save_config)
        self.save_button.pack(side="right")
        ctk.CTkLabel(
            footer,
            text=f"Changes apply now; Save writes them to {CONFIG_PATH}",
            font=("", 10),
            text_color=MUTED,
            anchor="w",
        ).pack(side="left")

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

        section = self.sections[name]
        section.holder.pack(fill="both", expand=True)
        self.active = name
        if section.on_show:
            section.on_show()

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

    # --- editing plumbing -----------------------------------------------------------------

    def _commit(self, path: str, value, apply: Callable[[object], None] | None = None) -> str | None:
        """Validate one edit on a throwaway copy, then apply it. Returns an error, or None.

        Going through `with_value` means a value the app could not start with never reaches the live
        configuration, and that the stored value is the normalised one ("debug" becomes "DEBUG").
        """
        try:
            candidate = with_value(CONFIG, path, value)
        except ConfigError as e:
            logger.debug(f"Rejected {path}={value!r}: {e}")
            return str(e)

        stored = get_value(candidate, path)
        set_value(CONFIG, path, stored)
        if apply:
            apply(stored)

        logger.debug(f"{path} set to {stored!r}")
        return None

    def _entry_row(
        self,
        section: SettingsSection,
        title,
        subtitle,
        path: str,
        *,
        parse: Callable[[str], object] = parse_plain,
        display: Callable[[object], str] = as_text,
        width: int = 220,
        show: str | None = None,
        apply: Callable[[object], None] | None = None,
    ) -> SettingsRow:
        """A typed text field bound to one configuration key.

        Commits on Enter and on losing focus, and redisplays the stored value either way: a refused
        edit reverts on screen rather than sitting there looking accepted.
        """
        entry = ctk.CTkEntry(section.card, width=width, show=show)
        row = section.add_row(title, subtitle, entry)

        def redisplay():
            entry.delete(0, "end")
            entry.insert(0, display(get_value(CONFIG, path)))

        def commit(_event=None):
            try:
                value = parse(entry.get())
            except ValueError as e:
                row.show_error(str(e))
                redisplay()
                return

            error = self._commit(path, value, apply)
            if error:
                row.show_error(error)
            else:
                row.clear_error()
            redisplay()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        redisplay()
        section.register(path, redisplay)
        row.entry = entry  # tests and the visibility toggles reach the widget through the row
        return row

    def _choice_row(
        self,
        section: SettingsSection,
        title,
        subtitle,
        path: str,
        values,
        *,
        parse: Callable[[str], object] = parse_plain,
        display: Callable[[object], str] = as_text,
        width: int = 220,
        apply: Callable[[object], None] | None = None,
    ) -> SettingsRow:
        """A fixed set of choices. There is nothing to revert: an invalid value is unreachable."""
        options = list(values)
        menu = ctk.CTkOptionMenu(section.card, values=options, width=width)
        row = section.add_row(title, subtitle, menu)

        def redisplay():
            current = display(get_value(CONFIG, path))
            if current not in options:  # a hand-edited value the list does not offer
                options.append(current)
                menu.configure(values=options)
            menu.set(current)

        def commit(label):
            error = self._commit(path, parse(label), apply)
            if error:
                row.show_error(error)
            else:
                row.clear_error()
            redisplay()

        menu.configure(command=commit)
        redisplay()
        section.register(path, redisplay)
        row.menu = menu
        return row

    def _combo_row(
        self,
        section: SettingsSection,
        title,
        subtitle,
        path: str,
        *,
        parse: Callable[[str], object] = parse_plain,
        display: Callable[[object], str] = as_text,
        width: int = 220,
        apply: Callable[[object], None] | None = None,
    ) -> SettingsRow:
        """Free text with a dropdown of suggestions, for keys whose useful values are discoverable."""
        combo = ctk.CTkComboBox(section.card, values=[], width=width)
        row = section.add_row(title, subtitle, combo)

        def redisplay():
            combo.set(display(get_value(CONFIG, path)))

        def commit(_event=None):
            try:
                value = parse(combo.get())
            except ValueError as e:
                row.show_error(str(e))
                redisplay()
                return

            error = self._commit(path, value, apply)
            if error:
                row.show_error(error)
            else:
                row.clear_error()
            redisplay()

        combo.configure(command=lambda _label: commit())
        combo.bind("<Return>", commit)
        combo.bind("<FocusOut>", commit)
        redisplay()
        section.register(path, redisplay)
        row.combo = combo
        return row

    def _switch_row(
        self,
        section: SettingsSection,
        title,
        subtitle,
        path: str,
        *,
        apply: Callable[[object], None] | None = None,
    ) -> SettingsRow:
        """An on/off setting. A switch cannot produce an invalid value, so there is nothing to revert."""
        switch = ctk.CTkSwitch(section.card, text="")
        row = section.add_row(title, subtitle, switch)

        def redisplay():
            switch.select() if get_value(CONFIG, path) else switch.deselect()

        def commit():
            self._commit(path, bool(switch.get()), apply)
            redisplay()

        switch.configure(command=commit)
        redisplay()
        section.register(path, redisplay)
        row.switch = switch
        return row

    def _reset_row(self, section: SettingsSection) -> SettingsRow:
        """Put this card's keys back to their defaults, which also drops them from the saved file."""
        return section.add_row(
            "Reset this section",
            "Back to the built-in defaults for the settings above",
            ctk.CTkButton(
                section.card,
                text="Reset",
                width=110,
                fg_color="transparent",
                border_width=1,
                command=lambda: self._reset_section(section),
            ),
        )

    def _reset_section(self, section: SettingsSection):
        reference = config_defaults()
        for path, _refresh in section.bindings:
            set_value(CONFIG, path, get_value(reference, path))
        for _path, refresh in section.bindings:
            refresh()
        for row in section.rows:
            row.clear_error()
        if section.after_reset:
            section.after_reset()

        logger.info(f"{section.title} settings reset to defaults")

    # --- background work ------------------------------------------------------------------

    def _in_background(self, work: Callable[[], object], done: Callable[[bool, object], None]):
        """Run `work` off the Tk thread and hand `(ok, result)` to `done` on the Tk thread."""
        results: queue.Queue[tuple[bool, object]] = queue.Queue()

        def run():
            try:
                results.put((True, work()))
            except Exception as e:  # noqa: BLE001 -- sensor sources and pyserial raise arbitrary types
                results.put((False, e))

        def poll():
            try:
                ok, payload = results.get_nowait()
            except queue.Empty:
                self._poll_later(poll)
                return

            done(ok, payload)

        threading.Thread(target=run, daemon=True).start()
        self._poll_later(poll)

    def _poll_later(self, callback):
        """Schedule a poll, unless the view has gone away while the worker was busy."""
        try:
            if self.winfo_exists():
                self.after(UI_POLL_MS, callback)
        except TclError:
            pass

    # --- Curve ----------------------------------------------------------------------------

    def _build_curve_section(self):
        section = self._add_section("Curve")

        selected = ctk.CTkFrame(section.card, fg_color="transparent")
        section.add_row("Selected point", "Click a point, or type exact values and press Enter", selected)

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
            text="click to select · drag the selected point · double-click to add"
            " · right-click or Del to remove · Ctrl+Z to undo",
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
        section.add_row("Control mode", "Auto follows the curve; Manual is fixed", self.mode_switch)
        section.register("control.mode", self._apply_configured_mode)

        # Both children of the holder, not of the card. Building them on the card and packing them
        # with in_=holder leaves them siblings of the holder and lower in the stacking order, so the
        # holder is drawn over them: they report themselves as mapped and nothing is on screen.
        holder = ctk.CTkFrame(section.card, fg_color="transparent")
        self.manual_row = section.add_row("Manual speed", "Used only in Manual mode", holder)

        self.manual_slider = ctk.CTkSlider(
            holder, from_=0, to=100, number_of_steps=100, width=190, command=self._on_manual_change
        )
        self.manual_slider.pack(side="left")

        # Drag for a rough value, type for an exact one -- the same pairing as the curve editor.
        self.manual_value = ctk.CTkEntry(holder, width=52, justify="right")
        self.manual_value.pack(side="left", padx=(10, 4))
        self.manual_value.bind("<Return>", self._apply_manual_entry)
        self.manual_value.bind("<FocusOut>", self._apply_manual_entry)
        ctk.CTkLabel(holder, text="%", font=("", 11), text_color=MUTED).pack(side="left")

        # Registered like any other key: this card is where control.mode and control.manual_speed
        # are edited, even though they are a switch and a slider rather than generic rows.
        section.register("control.manual_speed", self._apply_configured_mode)
        self._show_manual_speed()

    def _on_mode_change(self, value):
        # Manual speed is remembered across runs, so selecting Manual applies the speed the user
        # last chose. Seeding it from the current automatic speed would quietly overwrite that.
        self.controller.mode = Mode.AUTO if value == "Auto" else Mode.MANUAL
        self._show_manual_speed()

    def _on_manual_change(self, value):
        self.controller.manual_speed = int(value)
        self._show_manual_speed()

    def _apply_manual_entry(self, _event=None):
        """Take an exact percentage typed into the box. Anything unusable reverts to what is set."""
        try:
            speed = round(float(self.manual_value.get().strip().replace(",", ".").rstrip("% ")))
        except ValueError:
            logger.debug(f"Ignoring non-numeric manual speed {self.manual_value.get()!r}")
            self._show_manual_speed()
            return

        self.controller.manual_speed = speed  # the setter clamps to 0..100
        self._show_manual_speed()

    def _show_manual_speed(self):
        speed = self.controller.manual_speed
        self.manual_slider.set(speed)
        if self.manual_value.get() != str(speed):
            self.manual_value.delete(0, "end")
            self.manual_value.insert(0, str(speed))

    def _apply_configured_mode(self):
        """Hand the configured mode and speed to the controller, then redraw the card.

        This card edits the controller rather than the configuration object, so a change arriving the
        other way -- a section Reset, or a test restoring the configuration -- has to be passed on and
        not merely redrawn.
        """
        self.controller.mode = Mode(CONFIG.control.mode)
        self.controller.manual_speed = CONFIG.control.manual_speed
        self.sync_mode()

    def sync_mode(self):
        """Reflect a mode or speed change made from Home or the tray menu.

        All three surfaces drive the same controller, so this card has to be told when one of the
        others moved it; otherwise it shows a stale mode and the next click on it reapplies that.
        """
        self.mode_switch.set("Auto" if self.controller.mode == Mode.AUTO else "Manual")
        self._show_manual_speed()

    # --- Sensors --------------------------------------------------------------------------

    def _build_sensors_section(self):
        section = self._add_section("Sensors")

        self.provider_row = self._choice_row(
            section,
            "Temperature source",
            "Which monitoring app the readings come from",
            "sensors.provider",
            sorted(PROVIDER_NAMES),
            # The menu shows the product name; the file keeps the code name.
            display=lambda name: PROVIDER_LABELS.get(str(name), str(name)),
            parse=lambda label: PROVIDER_NAMES.get(label, label),
            width=260,
            apply=self._apply_provider,
        )
        # Its own label rather than the row's error line: a successful pick clears that line right
        # after applying, and this note has to outlive the pick that caused it.
        self.admin_label = ctk.CTkLabel(
            self.provider_row.text, text=ADMIN_NOTE, font=("", 10), text_color=ERROR_COLOR, anchor="w"
        )

        self.cpu_filter_row = self._combo_row(
            section,
            "CPU sensor filter",
            "Hottest matching sensor drives the curve",
            "sensors.cpu_filter",
            parse=parse_required,
            apply=lambda _value: self.refresh_sensor_match(),
        )
        self.gpu_filter_row = self._combo_row(
            section,
            "GPU sensor filter",
            "Same, for the GPU",
            "sensors.gpu_filter",
            parse=parse_required,
            apply=lambda _value: self.refresh_sensor_match(),
        )

        # A filter matching nothing reads 0 °C and quietly holds the fan at its minimum, so what the
        # filters currently catch belongs on screen rather than in the log. The reading goes in the
        # row's own text column, not beside the button: grid sizes the control column to the widest
        # control in the card, and a wide control here would squeeze every description above.
        row = section.add_row(
            "Filter match",
            "What the filters above find right now",
            ctk.CTkButton(section.card, text="Test", width=110, command=self.refresh_sensor_match),
        )
        self.match_label = ctk.CTkLabel(row.text, text="not checked yet", font=("", 11), text_color=MUTED, anchor="w")
        self.match_label.pack(anchor="w", pady=(2, 0))

        self.lhm_rows = [
            self._entry_row(
                section,
                "Web server URL",
                "Address of the Remote Web Server",
                "sensors.lhm_web.url",
                parse=parse_required,
                width=FIELD_WIDTH,
            ),
            self._entry_row(
                section,
                "Web server timeout",
                "Seconds to wait for a reading",
                "sensors.lhm_web.timeout",
                parse=parse_float,
                width=100,
            ),
            self._entry_row(
                section,
                "Web server username",
                "Leave empty unless authentication is enabled",
                "sensors.lhm_web.username",
            ),
            self._entry_row(
                section,
                "Web server password",
                f"Stored as plain text in {CONFIG_PATH.name}",
                "sensors.lhm_web.password",
                show="*",
            ),
        ]

        self._reset_row(section)
        section.on_show = self.refresh_sensor_match
        section.after_reset = self._on_provider_reset
        self._apply_lhm_visibility()
        self._apply_admin_note()

    def _apply_provider(self, name):
        self._apply_lhm_visibility()
        self._apply_admin_note()
        try:
            self.controller.sensors = name
        except ValueError as e:
            logger.error(f"Could not switch the sensor source: {e}")
            self.match_label.configure(text=str(e), text_color=ERROR_COLOR)
            return

        self.refresh_sensor_match()

    def _on_provider_reset(self):
        self._apply_provider(CONFIG.sensors.provider)

    def _apply_lhm_visibility(self):
        """The web-server keys only mean anything for the lhm-web source."""
        visible = CONFIG.sensors.provider == LHM_WEB
        for row in self.lhm_rows:
            row.set_visible(visible)

    def _apply_admin_note(self):
        """Say next to the selector when the chosen source cannot work in a process that is not elevated."""
        if lacks_admin_rights(find_provider(CONFIG.sensors.provider)):
            if not self.admin_label.winfo_manager():
                self.admin_label.pack(anchor="w", pady=(2, 0))
        elif self.admin_label.winfo_manager():
            self.admin_label.pack_forget()

    def refresh_sensor_match(self):
        """Read the active source once and report what the two filters catch."""
        self.match_label.configure(text="checking…", text_color=MUTED)
        self._in_background(self.controller.sensors.get_temperatures, self._show_sensor_match)

    def _show_sensor_match(self, ok: bool, payload):
        if not ok:
            self.match_label.configure(text=f"read failed: {payload}", text_color=ERROR_COLOR)
            return

        readings: dict[str, float] = payload or {}
        for row, key in ((self.cpu_filter_row, "cpu"), (self.gpu_filter_row, "gpu")):
            row.combo.configure(values=sorted(readings) or [get_value(CONFIG, f"sensors.{key}_filter")])

        parts = []
        missing = False
        for label, needle in (("CPU", CONFIG.sensors.cpu_filter), ("GPU", CONFIG.sensors.gpu_filter)):
            hits = [value for name, value in readings.items() if needle in name]
            if hits:
                unit = "sensor" if len(hits) == 1 else "sensors"
                parts.append(f"{label}: {len(hits)} {unit}, max {max(hits):g} °C")
            else:
                parts.append(f"{label}: nothing matches “{needle}”")
                missing = True

        self.match_label.configure(text=" · ".join(parts), text_color=ERROR_COLOR if missing else MUTED)

    # --- Device ---------------------------------------------------------------------------

    def _build_device_section(self):
        section = self._add_section("Device")

        # None of these apply until the port is reopened; the Connection row at the bottom says so
        # once rather than repeating it in six descriptions that would then not fit.
        self.port_row = self._combo_row(
            section, "Serial port", "Tried first, before searching by name", "device.port", parse=parse_required
        )
        self._entry_row(
            section,
            "Device description",
            "Matched when the port moves",
            "device.name",
            parse=parse_optional,
            width=FIELD_WIDTH,
        )
        self._entry_row(
            section,
            "Device serial number",
            "Matched as a substring; wins over the description",
            "device.serial",
            parse=parse_optional,
        )
        self._choice_row(section, "Baud rate", "", "device.baudrate", BAUDRATES, parse=parse_int, width=140)
        self._entry_row(
            section, "Read timeout", "Seconds to wait for a reply", "device.timeout", parse=parse_float, width=100
        )
        self._entry_row(
            section,
            "PWM command",
            "Tasmota command carrying the fan level",
            "device.pwm_command",
            parse=parse_required,
        )

        row = section.add_row(
            "Connection",
            "The settings above take effect when the port is reopened",
            ctk.CTkButton(section.card, text="Reconnect", width=110, command=self.reconnect),
        )
        self.reconnect_button = row.control
        self.connection_label = ctk.CTkLabel(row.text, text="", font=("", 11), text_color=MUTED, anchor="w")
        self.connection_label.pack(anchor="w", pady=(2, 0))

        self._reset_row(section)
        section.on_show = self._on_device_shown
        self.refresh_connection()

    def _on_device_shown(self):
        self.refresh_connection()
        try:
            ports = sorted(port.device for port in comports())
        except (OSError, ValueError) as e:  # enumerating ports can fail on a wedged driver
            logger.debug(f"Could not list serial ports: {e}")
            return

        self.port_row.combo.configure(values=ports or [CONFIG.device.port])

    def refresh_connection(self):
        connected = self.controller.connected
        port = self.controller.port or CONFIG.device.port
        self.connection_label.configure(
            text=f"connected on {port}" if connected else f"not connected ({port})",
            text_color=MUTED if connected else ERROR_COLOR,
        )

    def reconnect(self):
        """Ask the shell to reopen the port, then report what happened."""
        if not self.on_reconnect:
            logger.debug("No reconnect handler wired up")
            return

        self.reconnect_button.configure(state="disabled")
        self.connection_label.configure(text="reconnecting…", text_color=MUTED)
        future = self.on_reconnect()

        def finish():
            if future is not None and not future.done():
                self._poll_later(finish)
                return

            self.reconnect_button.configure(state="normal")
            self.refresh_connection()

        self._poll_later(finish)

    # --- Control --------------------------------------------------------------------------

    def _build_control_section(self):
        section = self._add_section("Control")

        self._entry_row(
            section,
            "Response time",
            "Seconds between readings; lower reacts sooner",
            "control.delay",
            parse=parse_float,
            width=100,
        )
        self._entry_row(
            section,
            "Smoothing window",
            "Readings the rolling median covers; 1 disables it",
            "control.temp_window",
            parse=parse_int,
            width=100,
            apply=lambda value: setattr(self.controller, "temp_window", value),
        )
        self._entry_row(
            section,
            "Re-read the device every",
            "Ticks between reads of the device's value; 0 never",
            "control.resync_every",
            parse=parse_int,
            width=100,
        )
        self._entry_row(
            section,
            "Max ramp-down step",
            "Largest drop per tick; speeding up is never limited",
            "control.max_step",
            parse=parse_int,
            width=100,
        )
        self._entry_row(
            section,
            "Hysteresis",
            "Ignore changes smaller than this many percent",
            "control.ignore_less_than",
            parse=parse_int,
            width=100,
        )

        self._reset_row(section)
        section.after_reset = lambda: setattr(self.controller, "temp_window", CONFIG.control.temp_window)

    # --- Logging --------------------------------------------------------------------------

    def _build_logging_section(self):
        section = self._add_section("Logging")

        self._choice_row(
            section,
            "Log level",
            "DEBUG records every reading; INFO records changes",
            "logging.level",
            LOG_LEVELS,
            width=140,
            apply=lambda _value: self._apply_logging(),
        )
        self._entry_row(
            section,
            "Log file",
            "Empty means no log file is written",
            "logging.file",
            parse=parse_optional,
            width=FIELD_WIDTH,
            apply=lambda _value: self._apply_logging(),
        )
        section.add_row(
            "Log folder",
            "Open where the log goes, or would go",
            ctk.CTkButton(section.card, text="Open", width=110, command=self._open_log_folder),
        )
        section.add_row(
            "Use the default path",
            "Fill in the standard location under %LOCALAPPDATA%",
            ctk.CTkButton(section.card, text="Use default", width=110, command=self._use_default_log_file),
        )

        self._reset_row(section)
        section.after_reset = self._apply_logging

    def _apply_logging(self):
        """Rebuild the handlers so a level or path change takes effect on the next record."""
        reapply_log_settings()

    def _use_default_log_file(self):
        """Turn the log file on at the standard location, for when something needs diagnosing."""
        self._commit("logging.file", str(default_log_file()), lambda _value: self._apply_logging())
        for section in self.sections.values():
            for path, refresh in section.bindings:
                if path == "logging.file":
                    refresh()

    def _open_log_folder(self):
        folder = (Path(CONFIG.logging.file) if CONFIG.logging.file else default_log_file()).parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except (OSError, AttributeError) as e:
            logger.error(f"Could not open {folder}: {e}")

    # --- Display --------------------------------------------------------------------------

    def _build_display_section(self):
        section = self._add_section("Display")

        self.span_menu = ctk.CTkOptionMenu(
            section.card, values=list(SPAN_LABELS), width=200, command=self._on_span_change
        )
        self.span_menu.set(format_span(self.history_window))
        section.add_row(
            "Graph timeline",
            "How far back the graph on Home reaches",
            self.span_menu,
        )

        self._switch_row(
            section,
            "Start minimized",
            "Launch straight to the tray, without showing the window",
            "ui.minimize_on_launch",
        )
        self._switch_row(
            section,
            "Minimize to tray",
            "Off leaves the window in the taskbar instead",
            "ui.hide_to_tray_on_minimize",
        )

        self._reset_row(section)

    def _on_span_change(self, label):
        seconds = SPAN_LABELS[label]
        self.history_window = seconds
        if self.on_history_window:
            self.on_history_window(seconds)
        logger.debug(f"Graph timeline set to {seconds} s")

    def _save_config(self):
        """Persist what is currently in memory. Only non-default values reach the file."""
        CONFIG.control.curve = [list(point) for point in self.controller.curve]
        CONFIG.ui.history_window = self.history_window
        try:
            target = save_config(CONFIG)
        except OSError as e:
            logger.error(f"Could not save the configuration: {e}")
            self.save_button.configure(text="Failed")
        else:
            logger.info(f"Configuration saved to {target}")
            self.save_button.configure(text="Saved")

        self.after(1800, lambda: self.save_button.configure(text="Save"))
