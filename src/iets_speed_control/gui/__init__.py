"""GUI widgets and the tray application.

Kept out of entrypoints/gui.pyw so the code is ordinary .py: ruff does not scan .pyw by default,
which previously hid lint findings in the GUI.
"""

import customtkinter as ctk

from .app import GUIApp
from .curve_editor import CurveEditor
from .history import TemperatureHistory
from .settings import SettingsView
from .status import StatusPanel

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

__all__ = ["CurveEditor", "GUIApp", "SettingsView", "StatusPanel", "TemperatureHistory"]
