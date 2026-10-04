"""Colours and metrics shared by the GUI widgets."""

APP_NAME = "IETS Speed Control"

BACKGROUND = "#1c1c1c"
CARD = "#252525"
SIDEBAR = "#202020"
ROW_LINE = "#333333"
NAV_ACTIVE = "#323232"
NAV_HOVER = "#2c2c2c"
EXIT_HOVER = "#7a2e2e"  # a destructive action should not hover like a destination
MUTED = "#8f8f8f"
ERROR_COLOR = "#d05050"
GRID = "#343434"
CURVE_LINE = "#d8d8d8"

ACCENT_COLOR = "#a5e12a"
MAX_COLOR = ACCENT_COLOR  # the number that drives the fan
FAN_COLOR = "#e8e8e8"

# Left navigation rail, per the Material 3 collapsed/expanded rail pattern.
RAIL_EXPANDED_WIDTH = 200
RAIL_COLLAPSED_WIDTH = 56

WINDOW_SIZE = (900, 600)
# The floor is set by the settings rows: description on the left, control on the right, and below
# this width the descriptions get clipped rather than wrapped. See test_no_description_is_cut_off.
WINDOW_MIN_SIZE = (800, 460)
FIELD_WIDTH = 220  # widest entry in a settings row; wider than this squeezes the descriptions

HISTORY_WINDOW_SECONDS = 600  # visible span of the main-window graph
HISTORY_WINDOW_CHOICES = (60, 300, 600, 1800, 3600)
UI_POLL_MS = 100  # how often the Tk thread drains controller updates
