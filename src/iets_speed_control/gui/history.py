"""Scrolling temperature history for the main window.

Samples are kept with timestamps and pruned by age, not by count: the visible span then stays
correct even if DELAY changes or the control loop misses a tick.
"""

import time
from collections import deque

import customtkinter as ctk

from .theme import CARD, GRID, HISTORY_WINDOW_SECONDS, MAX_COLOR, MUTED

TEMP_FLOOR, TEMP_CEILING = 30, 100
GRID_MARKS = (50, 75, 100)
HARD_SAMPLE_CAP = 50_000  # backstop against a huge window combined with a tiny delay
POINTS_PER_PIXEL = 2  # above this the series is decimated before drawing


class TemperatureHistory(ctk.CTkFrame):
    """The maximum the filters matched, over the last `window_seconds`."""

    def __init__(self, master, width=380, height=88, window_seconds=HISTORY_WINDOW_SECONDS, clock=time.monotonic):
        super().__init__(master, fg_color="transparent")
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self.samples: deque[tuple[float, float]] = deque(maxlen=HARD_SAMPLE_CAP)

        self.canvas = ctk.CTkCanvas(self, width=width, height=height, bg=CARD, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

    # --- data -----------------------------------------------------------------------------

    def add(self, value):
        """Record one reading. Drawing is the caller's business.

        A drain can replay a dozen readings into one visible frame, and redrawing per reading meant
        a dozen full canvas rebuilds to show the last of them. The caller redraws once when it is
        done adding.
        """
        self.samples.append((self._clock(), float(value)))
        self._prune()

    def _prune(self):
        cutoff = self._clock() - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def set_window(self, seconds):
        """Change the visible span. Shrinking drops samples that no longer fit."""
        self.window_seconds = float(seconds)
        self._prune()
        self.redraw()

    # --- drawing --------------------------------------------------------------------------

    def _series(self, width):
        """Return the line in canvas coordinates, decimated to suit the width."""
        now = self._clock()
        span = TEMP_CEILING - TEMP_FLOOR
        height = self.canvas.winfo_height() or int(self.canvas["height"])

        # Round the step up so the drawn point count never exceeds the target.
        target = max(1, int(width * POINTS_PER_PIXEL))
        step = max(1, -(-len(self.samples) // target))
        path = []
        for index in range(0, len(self.samples), step):
            stamp, value = self.samples[index]
            # Newest sample sits at the right edge; older ones scroll off to the left.
            x = width - (now - stamp) / self.window_seconds * width
            clamped = min(max(value, TEMP_FLOOR), TEMP_CEILING)
            path.extend((x, height - (clamped - TEMP_FLOOR) / span * height))

        return path

    def redraw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width() or int(canvas["width"])
        height = canvas.winfo_height() or int(canvas["height"])
        span = TEMP_CEILING - TEMP_FLOOR

        for mark in GRID_MARKS:
            y = height - (mark - TEMP_FLOOR) / span * height
            canvas.create_line(0, y, width, y, fill=GRID)
            canvas.create_text(4, y - 7, text=str(mark), anchor="w", fill=MUTED, font=("", 8))

        canvas.create_text(4, height - 8, text=self.span_label(), anchor="w", fill=MUTED, font=("", 8))

        # A fresh start only fills a sliver at the right edge; say so rather than look broken.
        if self.filled_fraction() < 0.05:
            canvas.create_text(width / 2, height / 2, text="collecting…", anchor="center", fill=MUTED, font=("", 10))

        path = self._series(width)
        # A single sample cannot be a line, and an empty history must still draw the grid.
        if len(path) >= 4:
            canvas.create_line(*path, fill=MAX_COLOR, width=2)

        if self.samples:
            _stamp, value = self.samples[-1]
            canvas.create_text(
                width - 6, 8, text=f"Max {value:.0f}°", anchor="ne", fill=MAX_COLOR, font=("", 10, "bold")
            )

    def filled_fraction(self) -> float:
        """How much of the visible span the collected samples actually cover, 0..1."""
        if len(self.samples) < 2:
            return 0.0

        return min(1.0, (self.samples[-1][0] - self.samples[0][0]) / self.window_seconds)

    def span_label(self) -> str:
        minutes = self.window_seconds / 60
        return f"{minutes:g} min" if minutes >= 1 else f"{self.window_seconds:g} s"
