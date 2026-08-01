"""Draggable temperature -> fan percent curve, drawn on a CTkCanvas.

customtkinter has no chart widget, but CTkCanvas is a tkinter.Canvas subclass, so the curve is
drawn and hit-tested by hand.
"""

import customtkinter as ctk

from .theme import CARD, CPU_COLOR, CURVE_LINE, GRID, MUTED

TEMP_MIN, TEMP_MAX = 0, 100
PCT_MIN, PCT_MAX = 0, 100
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 46, 18, 16, 30
HIT_RADIUS = 11
MIN_POINTS = 2
MIN_TEMP_GAP = 1  # keeps two points from landing on the same temperature
MAX_UNDO = 50  # generous for hand-editing a curve, and keeps the history from growing without bound

# Nothing interesting happens below 30 C, so that stretch is squashed into a narrow band and the rest
# of the width goes to the range worth editing. Deliberately a broken axis rather than a logarithmic
# one: a log scale expands the cold end instead of compressing it, and it would bend 30-100 C too --
# exactly where the curve needs to be read and dragged precisely. Below the knee the axis is still
# linear, just steeper, so a point parked down at 1 C stays visible and draggable.
TEMP_KNEE = 30
KNEE_FRACTION = 0.08  # share of the plot width given to everything below the knee
TEMP_TICKS = (0, TEMP_KNEE, 40, 50, 60, 70, 80, 90, TEMP_MAX)


def snap(value) -> int:
    """Curve points are whole degrees and whole percent: nothing finer is useful for a fan."""
    return round(float(value))


def temp_to_fraction(temp: float) -> float:
    """Where a temperature sits across the plot, 0 at the left edge and 1 at the right."""
    if temp <= TEMP_KNEE:
        return (temp - TEMP_MIN) / (TEMP_KNEE - TEMP_MIN) * KNEE_FRACTION

    return KNEE_FRACTION + (temp - TEMP_KNEE) / (TEMP_MAX - TEMP_KNEE) * (1 - KNEE_FRACTION)


def fraction_to_temp(fraction: float) -> float:
    """The inverse of temp_to_fraction, so a drag lands on the temperature under the cursor."""
    if fraction <= KNEE_FRACTION:
        return TEMP_MIN + fraction / KNEE_FRACTION * (TEMP_KNEE - TEMP_MIN)

    return TEMP_KNEE + (fraction - KNEE_FRACTION) / (1 - KNEE_FRACTION) * (TEMP_MAX - TEMP_KNEE)


class CurveEditor(ctk.CTkFrame):
    """Curve editor. Click a point to select it, drag the selected one to move it, double-click to
    add one, right-click or Delete to remove one."""

    def __init__(self, master, points, on_change=None, width=560, height=260):
        super().__init__(master, fg_color="transparent")
        self.points = sorted((snap(t), snap(p)) for t, p in points)
        self.on_change = on_change
        self.selected = 0
        self._dragging = False
        self._drag_recorded = False
        # Undo history, in memory only: it covers this editing session, not the saved file.
        self._history: list[tuple[list[tuple[int, int]], int]] = []

        # takefocus is what makes Delete work: a canvas with it set claims keyboard focus when
        # clicked, so no explicit focus_set is needed. The binding stays on the canvas rather than
        # the window, otherwise Delete would eat a point while the user edits the Temp/% entries.
        self.canvas = ctk.CTkCanvas(self, width=width, height=height, bg=CARD, highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Delete>", self._on_delete)
        self.canvas.bind("<Control-z>", self._on_undo)

    # --- undo -----------------------------------------------------------------------------

    def _remember(self):
        """Snapshot the curve before changing it. Points are tuples, so a shallow copy is enough."""
        self._history.append((list(self.points), self.selected))
        if len(self._history) > MAX_UNDO:
            del self._history[0]

    def _forget_if_unchanged(self):
        """Drop the snapshot again when the change turned out to be a no-op.

        Typing the value a point already has, or dragging it against a neighbour it is already
        touching, would otherwise leave an undo step that appears to do nothing when used.
        """
        if self._history and self._history[-1][0] == self.points:
            self._history.pop()

    def undo(self) -> bool:
        """Restore the curve as it was before the last change. Returns False when there is none."""
        if not self._history:
            return False

        points, selected = self._history.pop()
        self.points = points
        self.selected = min(selected, len(self.points) - 1)
        self.redraw()
        return True

    def _on_undo(self, _event=None):
        self.undo()

    # --- model ----------------------------------------------------------------------------

    def set_points(self, points, notify=True):
        self._remember()
        self.points = sorted((snap(t), snap(p)) for t, p in points)
        self.selected = min(self.selected, len(self.points) - 1)
        self._forget_if_unchanged()
        self.redraw(notify=notify)

    def update_selected(self, temp=None, pct=None):
        """Set the selected point numerically, applying the same rules as dragging."""
        self._remember()
        current_temp, current_pct = self.points[self.selected]
        temp = current_temp if temp is None else snap(temp)
        pct = current_pct if pct is None else snap(pct)
        self.points[self.selected] = (self._clamp_temp(temp), min(max(pct, PCT_MIN), PCT_MAX))
        self._push_neighbours(self.selected)
        self._forget_if_unchanged()
        self.redraw()

    def _clamp_temp(self, temp):
        low = self.points[self.selected - 1][0] + MIN_TEMP_GAP if self.selected > 0 else TEMP_MIN
        high = self.points[self.selected + 1][0] - MIN_TEMP_GAP if self.selected < len(self.points) - 1 else TEMP_MAX
        return min(max(temp, low), high)

    def _push_neighbours(self, index):
        """Keep the curve non-decreasing by dragging neighbours along, MSI Afterburner style.

        Raising a point lifts every point to its right to at least the same percentage; lowering one
        pulls every point to its left down to at most that percentage. The curve was monotonic
        before the move, so the first neighbour already satisfying the bound means the rest do too.
        """
        pct = self.points[index][1]

        for right in range(index + 1, len(self.points)):
            temp, other = self.points[right]
            if other >= pct:
                break
            self.points[right] = (temp, pct)

        for left in range(index - 1, -1, -1):
            temp, other = self.points[left]
            if other <= pct:
                break
            self.points[left] = (temp, pct)

    # --- coordinates ----------------------------------------------------------------------

    def _plot_box(self):
        width = self.canvas.winfo_width() or int(self.canvas["width"])
        height = self.canvas.winfo_height() or int(self.canvas["height"])
        return PAD_LEFT, PAD_TOP, width - PAD_RIGHT, height - PAD_BOTTOM

    def to_pixels(self, temp, pct):
        x0, y0, x1, y1 = self._plot_box()
        x = x0 + temp_to_fraction(temp) * (x1 - x0)
        y = y1 - (pct - PCT_MIN) / (PCT_MAX - PCT_MIN) * (y1 - y0)
        return x, y

    def to_values(self, x, y):
        x0, y0, x1, y1 = self._plot_box()
        temp = fraction_to_temp((x - x0) / max(1, x1 - x0))
        pct = PCT_MIN + (y1 - y) / max(1, y1 - y0) * (PCT_MAX - PCT_MIN)
        return temp, pct

    # --- drawing --------------------------------------------------------------------------

    def redraw(self, notify=True):
        canvas = self.canvas
        canvas.delete("all")
        x0, y0, x1, y1 = self._plot_box()

        for pct in range(0, 101, 10):
            _, y = self.to_pixels(0, pct)
            canvas.create_line(x0, y, x1, y, fill=GRID)
            canvas.create_text(
                x0 - 8, y, text="100 %" if pct == 100 else str(pct), anchor="e", fill=MUTED, font=("", 9)
            )
        # No 10 and 20 marks: they would sit on top of each other inside the squashed band. The knee
        # gets a brighter line so it is clear where the axis changes scale.
        for temp in TEMP_TICKS:
            x, _ = self.to_pixels(temp, 0)
            canvas.create_line(x, y0, x, y1, fill=CURVE_LINE if temp == TEMP_KNEE else GRID)
            if TEMP_MIN < temp < TEMP_MAX:
                canvas.create_text(x, y1 + 12, text=str(temp), anchor="n", fill=MUTED, font=("", 9))
        canvas.create_text(x0, y1 + 12, text=str(TEMP_MIN), anchor="nw", fill=MUTED, font=("", 9))
        canvas.create_text(x1, y1 + 12, text=f"{TEMP_MAX} °C", anchor="ne", fill=MUTED, font=("", 9))

        # Flat shelves before the first and after the last point mirror how the curve is evaluated.
        path = list(self.to_pixels(TEMP_MIN, self.points[0][1]))
        for temp, pct in self.points:
            path += list(self.to_pixels(temp, pct))
        path += list(self.to_pixels(TEMP_MAX, self.points[-1][1]))
        canvas.create_line(*path, fill=CURVE_LINE, width=2)

        for index, (temp, pct) in enumerate(self.points):
            x, y = self.to_pixels(temp, pct)
            radius = 9 if index == self.selected else 5
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=CPU_COLOR, outline="")

        if notify and self.on_change:
            self.on_change(list(self.points), self.selected)

    # --- interaction ----------------------------------------------------------------------

    def _nearest(self, x, y):
        best, best_distance = None, None
        for index, point in enumerate(self.points):
            px, py = self.to_pixels(*point)
            distance = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best, best_distance

    def _on_press(self, event):
        index, distance = self._nearest(event.x, event.y)
        if distance is None or distance > HIT_RADIUS:
            return

        # A press arms the drag only when it lands on the point that is already selected: the first
        # click picks a point, the second one moves it. On a curve with points a few degrees apart a
        # single click is easy to make by accident, and it used to move the fan curve.
        self._dragging = index == self.selected
        self._drag_recorded = False
        self.selected = index
        self.redraw()

    def _on_drag(self, event):
        if not self._dragging:
            return

        # One undo step per drag, taken on the first movement: a whole gesture is what the user did,
        # and a press that never moved is not a change at all.
        if not self._drag_recorded:
            self._remember()
            self._drag_recorded = True

        temp, pct = self.to_values(event.x, event.y)
        self.points[self.selected] = (self._clamp_temp(snap(temp)), snap(min(max(pct, PCT_MIN), PCT_MAX)))
        self._push_neighbours(self.selected)
        self.redraw()

    def _on_release(self, _event):
        if self._drag_recorded:
            self._forget_if_unchanged()  # dragged against a neighbour it was already touching
        self._dragging = False
        self._drag_recorded = False

    def _on_double_click(self, event):
        index, distance = self._nearest(event.x, event.y)
        if distance is not None and distance <= HIT_RADIUS:
            # A double-click on an existing point is not an insert. Tk delivers a quick second click
            # as this event rather than as a press, so it has to arm the drag exactly as a press
            # would: otherwise selecting a point and immediately dragging it does nothing.
            self._dragging = index == self.selected
            self._drag_recorded = False
            self.selected = index
            self.redraw()
            return

        temp, pct = self.to_values(event.x, event.y)
        point = (snap(min(max(temp, TEMP_MIN), TEMP_MAX)), snap(min(max(pct, PCT_MIN), PCT_MAX)))
        if any(abs(point[0] - existing) < MIN_TEMP_GAP for existing, _ in self.points):
            return

        self._remember()
        self.points.append(point)
        self.points.sort()
        self.selected = self.points.index(point)
        self._push_neighbours(self.selected)
        self.redraw()

    def _on_right_click(self, event):
        index, distance = self._nearest(event.x, event.y)
        if distance is not None and distance <= HIT_RADIUS:
            self.selected = index
            self.remove_selected()

    def _on_delete(self, _event=None):
        self.remove_selected()

    def remove_selected(self) -> bool:
        """Drop the selected point. Refuses below MIN_POINTS, which is what makes a curve a curve."""
        if len(self.points) <= MIN_POINTS:
            return False

        self._remember()
        self.points.pop(self.selected)
        self.selected = min(self.selected, len(self.points) - 1)
        self.redraw()
        return True
