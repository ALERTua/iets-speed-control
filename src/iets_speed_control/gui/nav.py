"""Left navigation rail, collapsible between icon-only and icon+label.

This is the current desktop convention: Material 3 Expressive deprecated the navigation drawer in
favour of a rail with collapsed and expanded configurations, pinned to the leading edge and always
visible. Sub-items are indented under their parent and only shown while expanded, since a collapsed
rail has no room for labels.
"""

import customtkinter as ctk

from .theme import EXIT_HOVER, NAV_ACTIVE, NAV_HOVER, RAIL_COLLAPSED_WIDTH, RAIL_EXPANDED_WIDTH, SIDEBAR

EXIT_ICON = "⏻"


class NavItem:
    """One destination. `parent` marks it as a sub-item of another key."""

    def __init__(self, key: str, label: str, icon: str = "", parent: str | None = None):
        self.key = key
        self.label = label
        self.icon = icon
        self.parent = parent


class NavigationRail(ctk.CTkFrame):
    """Vertical navigation. Click an item to select it, the chevron to collapse or expand."""

    def __init__(self, master, items: list[NavItem], on_select, collapsed: bool = False, on_exit=None):
        super().__init__(master, fg_color=SIDEBAR, corner_radius=0, width=RAIL_EXPANDED_WIDTH)
        self.items = items
        self.on_select = on_select
        self.on_exit = on_exit
        self.collapsed = collapsed
        self.selected: str | None = None

        self.pack_propagate(False)
        self._build()
        self._apply_width()

    # --- construction ---------------------------------------------------------------------

    def _build(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(14, 8))
        self.toggle_button = ctk.CTkButton(
            header,
            text="≡",
            width=32,
            height=32,
            corner_radius=6,
            fg_color="transparent",
            hover_color=NAV_HOVER,
            command=self.toggle,
        )
        self.toggle_button.pack(side="left")
        self.title_label = ctk.CTkLabel(header, text="IETS", font=("", 15, "bold"))
        self.title_label.pack(side="left", padx=(8, 0))

        self.buttons: dict[str, ctk.CTkButton] = {}
        for item in self.items:
            button = ctk.CTkButton(
                self,
                text="",
                anchor="w",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                hover_color=NAV_HOVER,
                command=lambda key=item.key: self.select(key),
            )
            self.buttons[item.key] = button

        # Packed against the bottom before any destination is, so it stays there whatever the list
        # above does. Destructive, so it is kept as far from the destinations as the rail allows.
        self.exit_button = ctk.CTkButton(
            self,
            text="",
            anchor="w",
            height=38,
            corner_radius=8,
            fg_color="transparent",
            hover_color=EXIT_HOVER,
            command=self._on_exit,
        )
        self.exit_button.pack(side="bottom", fill="x", padx=8, pady=(4, 10))

    def _on_exit(self):
        if self.on_exit:
            self.on_exit()

    def _item(self, key) -> NavItem:
        return next(item for item in self.items if item.key == key)

    # --- layout ---------------------------------------------------------------------------

    def _visible_items(self) -> list[NavItem]:
        """Top-level items always; sub-items only when expanded and their parent is active."""
        active_parent = None
        if self.selected is not None:
            item = self._item(self.selected)
            active_parent = item.parent or item.key

        visible = []
        for item in self.items:
            if item.parent is None or (not self.collapsed and item.parent == active_parent):
                visible.append(item)
        return visible

    def _relayout(self):
        for button in self.buttons.values():
            button.pack_forget()

        for item in self._visible_items():
            button = self.buttons[item.key]
            if self.collapsed:
                text, padx = item.icon or item.label[:1], 8
            elif item.parent is None:
                text, padx = f" {item.icon}  {item.label}".rstrip(), 8
            else:
                text, padx = f"      {item.label}", 8

            button.configure(text=text, anchor="center" if self.collapsed else "w")
            button.pack(fill="x", padx=padx, pady=2)
            active = item.key == self.selected
            button.configure(fg_color=NAV_ACTIVE if active else "transparent")

    def _apply_width(self):
        width = RAIL_COLLAPSED_WIDTH if self.collapsed else RAIL_EXPANDED_WIDTH
        self.configure(width=width)
        if self.collapsed:
            self.title_label.pack_forget()
        else:
            self.title_label.pack(side="left", padx=(8, 0))

        self.exit_button.configure(
            text=EXIT_ICON if self.collapsed else f" {EXIT_ICON}  Exit",
            anchor="center" if self.collapsed else "w",
        )
        self._relayout()

    # --- behaviour ------------------------------------------------------------------------

    def toggle(self):
        self.collapsed = not self.collapsed
        self._apply_width()

    def select(self, key, notify=True):
        self.selected = key
        self._relayout()
        if notify and self.on_select:
            self.on_select(key)
