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


class CurveEditor(ctk.CTkFrame):
    """Curve editor. Drag to move a point, double-click to add one, right-click to remove."""

    def __init__(self, master, points, on_change=None, width=560, height=260):
        super().__init__(master, fg_color="transparent")
        self.points = sorted((float(t), float(p)) for t, p in points)
        self.on_change = on_change
        self.selected = 0
        self._dragging = False

        self.canvas = ctk.CTkCanvas(self, width=width, height=height, bg=CARD, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

    # --- model ----------------------------------------------------------------------------

    def set_points(self, points, notify=True):
        self.points = sorted((float(t), float(p)) for t, p in points)
        self.selected = min(self.selected, len(self.points) - 1)
        self.redraw(notify=notify)

    def update_selected(self, temp=None, pct=None):
        """Set the selected point numerically, applying the same rules as dragging."""
        current_temp, current_pct = self.points[self.selected]
        temp = current_temp if temp is None else float(temp)
        pct = current_pct if pct is None else float(pct)
        self.points[self.selected] = (self._clamp_temp(temp), min(max(pct, PCT_MIN), PCT_MAX))
        self._push_neighbours(self.selected)
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
        x = x0 + (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN) * (x1 - x0)
        y = y1 - (pct - PCT_MIN) / (PCT_MAX - PCT_MIN) * (y1 - y0)
        return x, y

    def to_values(self, x, y):
        x0, y0, x1, y1 = self._plot_box()
        temp = TEMP_MIN + (x - x0) / max(1, x1 - x0) * (TEMP_MAX - TEMP_MIN)
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
        for temp in range(0, 101, 10):
            x, _ = self.to_pixels(temp, 0)
            canvas.create_line(x, y0, x, y1, fill=GRID)
            if 0 < temp < 100:
                canvas.create_text(x, y1 + 12, text=str(temp), anchor="n", fill=MUTED, font=("", 9))
        canvas.create_text(x1, y1 + 12, text="100 °C", anchor="ne", fill=MUTED, font=("", 9))

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
        if distance is not None and distance <= HIT_RADIUS:
            self.selected = index
            self._dragging = True
            self.redraw()

    def _on_drag(self, event):
        if not self._dragging:
            return

        temp, pct = self.to_values(event.x, event.y)
        self.points[self.selected] = (self._clamp_temp(temp), min(max(pct, PCT_MIN), PCT_MAX))
        self._push_neighbours(self.selected)
        self.redraw()

    def _on_release(self, _event):
        self._dragging = False

    def _on_double_click(self, event):
        _index, distance = self._nearest(event.x, event.y)
        if distance is not None and distance <= HIT_RADIUS:
            return  # a double-click on an existing point is not an insert

        temp, pct = self.to_values(event.x, event.y)
        point = (round(min(max(temp, TEMP_MIN), TEMP_MAX), 1), round(min(max(pct, PCT_MIN), PCT_MAX), 1))
        if any(abs(point[0] - existing) < MIN_TEMP_GAP for existing, _ in self.points):
            return

        self.points.append(point)
        self.points.sort()
        self.selected = self.points.index(point)
        self._push_neighbours(self.selected)
        self.redraw()

    def _on_right_click(self, event):
        if len(self.points) <= MIN_POINTS:
            return

        index, distance = self._nearest(event.x, event.y)
        if distance is not None and distance <= HIT_RADIUS:
            self.points.pop(index)
            self.selected = min(self.selected, len(self.points) - 1)
            self.redraw()
