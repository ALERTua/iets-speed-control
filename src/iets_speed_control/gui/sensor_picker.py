"""The sensor picker of the Sensors card: which sensors of which sources the fan follows.

One block per tracked source, in the order of sensors.tracked. A block lists every temperature its source
reports now, each with a check box: checked means tracked. A tracked sensor the source does not report now
stays listed, checked, so that it can still be unchecked. Below the blocks, any source not tracked yet can
be added.

The picker only proposes a new tracked set through `on_change`; the settings view validates and stores it,
then hands the stored set back through `set_tracked`. The values come in through `show_reads`.
"""

from collections.abc import Callable, Mapping, Sequence

import customtkinter as ctk

from ..sensors import PROVIDER_LABELS, PROVIDER_NAMES, find_provider, lacks_admin_rights
from ..util.tracking import SourceState
from .theme import ERROR_COLOR, MUTED, ROW_LINE

ADMIN_NOTE = "Needs admin rights: restart as administrator"
NO_READING = "no reading"
ASLEEP = "asleep"
NOTHING_TRACKED = "Nothing tracked: the fan runs at the default speed"
ADD_LABEL = "Add source"

# What a block header says about a source that did not answer with readings.
STATE_TEXT = {
    SourceState.SILENT: ("no answer", ERROR_COLOR),
    SourceState.NEEDS_ADMIN: ("no answer", ERROR_COLOR),
    SourceState.ASLEEP: ("asleep", MUTED),
}

# The stored form of sensors.tracked: source code name -> exact sensor labels.
TrackedLabels = dict[str, list[str]]


class SensorLine:
    """One sensor of a block: its check box and its value, on one row of the block's grid."""

    def __init__(self, master, row: int, on_toggle: Callable[[SensorLine], None]):
        self.label = ""
        self.checkbox = ctk.CTkCheckBox(master, text="", font=("", 12), command=lambda: on_toggle(self))
        self.checkbox.grid(row=row, column=0, sticky="w", pady=1)
        self.value = ctk.CTkLabel(master, text="", font=("", 11), text_color=MUTED, anchor="e", width=80)
        self.value.grid(row=row, column=1, sticky="e", padx=(8, 0), pady=1)

    @property
    def checked(self) -> bool:
        return bool(self.checkbox.get())

    def show(self, label: str, checked: bool, text: str, colour: str):
        if label != self.label:
            self.label = label
            self.checkbox.configure(text=label)
        if checked != self.checked:
            self.checkbox.select() if checked else self.checkbox.deselect()
        self.value.configure(text=text, text_color=colour)

    def destroy(self):
        self.checkbox.destroy()
        self.value.destroy()


class SourceBlock(ctk.CTkFrame):
    """One tracked source: a header with its name, its state and Remove, then one line per sensor."""

    def __init__(self, master, name: str, on_toggle, on_remove):
        super().__init__(master, fg_color="transparent")
        self.name = name
        self._on_toggle = on_toggle
        self.tracked: list[str] = []
        # None until the first read: a source not read yet has no verdict, so it must not show one.
        self.state: SourceState | None = None
        self.readings: dict[str, float] | None = None
        self.lines: list[SensorLine] = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x")
        self.title = ctk.CTkLabel(header, text=PROVIDER_LABELS.get(name, name), font=("", 13, "bold"), anchor="w")
        self.title.pack(side="left")
        self.remove_button = ctk.CTkButton(
            header,
            text="Remove",
            width=80,
            fg_color="transparent",
            border_width=1,
            command=lambda: on_remove(name),
        )
        self.remove_button.pack(side="right")
        self.state_label = ctk.CTkLabel(header, text="", font=("", 11), text_color=MUTED, anchor="w")
        self.state_label.pack(side="left", padx=(10, 0))

        # Said whatever the last read gave: a source that needs rights the app lacks never reads at all.
        self.admin_note = ctk.CTkLabel(self, text=ADMIN_NOTE, font=("", 10), text_color=ERROR_COLOR, anchor="w")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=(16, 0), pady=(2, 0))
        self.body.columnconfigure(0, weight=1)
        self.refresh_admin_note()

    def refresh_admin_note(self):
        needed = lacks_admin_rights(find_provider(self.name))
        if needed and not self.admin_note.winfo_manager():
            self.admin_note.pack(anchor="w", before=self.body)
        elif not needed and self.admin_note.winfo_manager():
            self.admin_note.pack_forget()

    def set_tracked(self, labels: Sequence[str]):
        self.tracked = list(labels)
        self._render()

    def show_read(self, state: SourceState, readings: Mapping[str, float]):
        self.state = state
        self.readings = dict(readings)
        self._render()

    @property
    def labels(self) -> list[str]:
        """Every sensor the block lists: what the source reports now, then the tracked ones it does not."""
        reported = list(self.readings or {})
        return reported + [label for label in self.tracked if label not in reported]

    def line_for(self, label: str) -> SensorLine | None:
        return next((line for line in self.lines if line.label == label), None)

    def _render(self):
        labels = self.labels
        self._resize(len(labels))
        for line, label in zip(self.lines, labels, strict=True):
            line.show(label, label in self.tracked, *self._value_of(label))

        text, colour = STATE_TEXT.get(self.state, ("", MUTED)) if self.state is not None else ("", MUTED)
        self.state_label.configure(text=text, text_color=colour)

    def _value_of(self, label: str) -> tuple[str, str]:
        if self.readings is None:
            return "", MUTED
        value = self.readings.get(label)
        if value is not None:
            return f"{value:g} °C", MUTED
        if self.state is SourceState.ASLEEP:
            return ASLEEP, MUTED
        return NO_READING, ERROR_COLOR

    def _resize(self, count: int):
        """Reuse the lines there are: a check box costs tens of milliseconds to build and to tear down."""
        for line in self.lines[count:]:
            line.destroy()
        del self.lines[count:]
        for row in range(len(self.lines), count):
            self.lines.append(SensorLine(self.body, row, self._toggled))

    def _toggled(self, line: SensorLine):
        self._on_toggle(self.name, line.label, line.checked)


class SensorPicker(ctk.CTkFrame):
    """The blocks and the Add control. `on_change` gets a proposed tracked set and returns an error, or None."""

    def __init__(self, master, on_change: Callable[[TrackedLabels], str | None]):
        super().__init__(master, fg_color="transparent")
        self._on_change = on_change
        self.tracked: TrackedLabels = {}
        self.blocks: dict[str, SourceBlock] = {}
        self.separators: list[ctk.CTkFrame] = []

        # Shown from the start: the picker begins with no block, and only a change of the sources repacks it.
        self.empty_label = ctk.CTkLabel(self, text=NOTHING_TRACKED, font=("", 11), text_color=MUTED, anchor="w")
        self.empty_label.pack(anchor="w")
        self.blocks_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.blocks_frame.pack(fill="x")

        self.footer = footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(footer, text=ADD_LABEL, font=("", 13), anchor="w").pack(side="left")
        self.add_button = ctk.CTkButton(footer, text="Add", width=70, command=self._add)
        self.add_button.pack(side="right")
        self.add_menu = ctk.CTkOptionMenu(footer, values=[""], width=260)
        self.add_menu.pack(side="right", padx=(0, 8))
        self.error_label = ctk.CTkLabel(self, text="", font=("", 10), text_color=ERROR_COLOR, anchor="w")

    # --- content ----------------------------------------------------------------------------------

    def set_tracked(self, tracked: Mapping[str, Sequence[str]]):
        """Show a tracked set that is already stored, keeping the block of every source that stays."""
        tracked = {name: list(labels) for name, labels in tracked.items()}
        if list(tracked) != list(self.blocks):
            self._arrange(list(tracked))
        for name, labels in tracked.items():
            self.blocks[name].set_tracked(labels)
        self.tracked = tracked
        self._offer_untracked()

    def show_reads(self, answers: Mapping[str, tuple[SourceState, Mapping[str, float]]]):
        """Show what each source answered; an answer for a source no longer tracked is ignored."""
        for name, (state, readings) in answers.items():
            block = self.blocks.get(name)
            if block is not None:
                block.show_read(state, readings)

    def refresh_admin_notes(self):
        for block in self.blocks.values():
            block.refresh_admin_note()

    def show_error(self, message: str | None):
        if message:
            self.error_label.configure(text=message)
            if not self.error_label.winfo_manager():
                self.error_label.pack(anchor="w", pady=(4, 0))
        elif self.error_label.winfo_manager():
            self.error_label.pack_forget()

    def _arrange(self, names: list[str]):
        for name in [name for name in self.blocks if name not in names]:
            self.blocks.pop(name).destroy()
        for separator in self.separators:
            separator.destroy()
        self.separators = []
        for block in self.blocks.values():
            block.pack_forget()

        # Repacked in the order of the tracked set, which is the order the file lists them in.
        for index, name in enumerate(names):
            if index:
                separator = ctk.CTkFrame(self.blocks_frame, height=1, fg_color=ROW_LINE)
                separator.pack(fill="x", pady=6)
                self.separators.append(separator)
            block = self.blocks.get(name)
            if block is None:
                block = SourceBlock(self.blocks_frame, name, on_toggle=self._toggle, on_remove=self._remove)
                self.blocks[name] = block
            block.pack(fill="x")

        if names and self.empty_label.winfo_manager():
            self.empty_label.pack_forget()
        elif not names and not self.empty_label.winfo_manager():
            self.empty_label.pack(anchor="w", before=self.blocks_frame)

    def _offer_untracked(self):
        labels = [PROVIDER_LABELS[name] for name in PROVIDER_LABELS if name not in self.tracked]
        if labels:
            self.add_menu.configure(values=labels, state="normal")
            if self.add_menu.get() not in labels:
                self.add_menu.set(labels[0])
            self.add_button.configure(state="normal")
        else:
            self.add_menu.configure(values=[""], state="disabled")
            self.add_menu.set("")
            self.add_button.configure(state="disabled")

    # --- editing ----------------------------------------------------------------------------------

    def _propose(self, tracked: TrackedLabels):
        self.show_error(self._on_change(tracked))

    def _toggle(self, source: str, label: str, checked: bool):
        tracked = {name: list(labels) for name, labels in self.tracked.items()}
        labels = tracked.setdefault(source, [])
        if checked and label not in labels:
            labels.append(label)
        elif not checked and label in labels:
            labels.remove(label)
        self._propose(tracked)

    def _remove(self, source: str):
        self._propose({name: list(labels) for name, labels in self.tracked.items() if name != source})

    def _add(self):
        name = PROVIDER_NAMES.get(self.add_menu.get())
        if name is None or name in self.tracked:
            return

        # Added with nothing picked: the source is read, so its sensors appear to choose from.
        self._propose({**{key: list(labels) for key, labels in self.tracked.items()}, name: []})
