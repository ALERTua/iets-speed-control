"""Colours and metrics shared by the GUI widgets."""

APP_NAME = "IETS Speed Control"

BACKGROUND = "#1c1c1c"
CARD = "#252525"
SIDEBAR = "#202020"
ROW_LINE = "#333333"
NAV_ACTIVE = "#323232"
NAV_HOVER = "#2c2c2c"
MUTED = "#8f8f8f"
GRID = "#343434"
CURVE_LINE = "#d8d8d8"

CPU_COLOR = "#a5e12a"
GPU_COLOR = "#4a9fd8"
FAN_COLOR = "#e8e8e8"

# Left navigation rail, per the Material 3 collapsed/expanded rail pattern.
RAIL_EXPANDED_WIDTH = 200
RAIL_COLLAPSED_WIDTH = 56

WINDOW_SIZE = (900, 600)
WINDOW_MIN_SIZE = (720, 460)

HISTORY_WINDOW_SECONDS = 600  # visible span of the main-window graph
HISTORY_WINDOW_CHOICES = (60, 300, 600, 1800, 3600)
UI_POLL_MS = 100  # how often the Tk thread drains controller updates
