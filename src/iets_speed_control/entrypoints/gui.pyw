"""
Modern tray GUI entrypoint using pystray and CustomTkinter.

Provides a system tray icon with menu and a GUI window for control.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Callable

import customtkinter as ctk
from PIL import Image
import pystray
from pystray import MenuItem as Item

from ..controller import Mode, SpeedController  # type: ignore[unresolved-import]
from ..util import env  # type: ignore[unresolved-import]

# Configure CustomTkinter
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_NAME = "IETS Speed Control"


class ControlWindow(ctk.CTkFrame):
    """Main control panel frame."""

    def __init__(self, master, controller: SpeedController, on_exit: Callable, gui_app: "GUIApp" = None):
        super().__init__(master)
        self.controller = controller
        self.on_exit = on_exit
        self.gui_app = gui_app

        self._build_ui()
        self._setup_callbacks()

    def _build_ui(self):
        """Build the UI components."""
        # Configure grid
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=0)
        self.rowconfigure(3, weight=0)
        self.rowconfigure(4, weight=1)

        # Mode selection
        mode_frame = ctk.CTkFrame(self)
        mode_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")

        mode_label = ctk.CTkLabel(mode_frame, text="Mode:", font=("", 14))
        mode_label.pack(side="left", padx=5)

        self.mode_var = ctk.StringVar(value=Mode.AUTO.value)
        self.auto_radio = ctk.CTkRadioButton(
            mode_frame, text="Auto", variable=self.mode_var,
            value=Mode.AUTO.value, command=self._on_mode_change
        )
        self.auto_radio.pack(side="left", padx=10)

        self.manual_radio = ctk.CTkRadioButton(
            mode_frame, text="Manual", variable=self.mode_var,
            value=Mode.MANUAL.value, command=self._on_mode_change
        )
        self.manual_radio.pack(side="left", padx=10)

        # Status frame
        status_frame = ctk.CTkFrame(self)
        status_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        self.status_label = ctk.CTkLabel(
            status_frame, text="Status: Disconnected",
            font=("", 12)
        )
        self.status_label.pack(pady=5)

        # Temperature display
        temp_frame = ctk.CTkFrame(self)
        temp_frame.grid(row=2, column=0, padx=10, pady=5, sticky="ew")

        self.cpu_label = ctk.CTkLabel(
            temp_frame, text="CPU: --°C", font=("", 14, "bold")
        )
        self.cpu_label.pack(side="left", padx=20, pady=10)

        self.gpu_label = ctk.CTkLabel(
            temp_frame, text="GPU: --°C", font=("", 14, "bold")
        )
        self.gpu_label.pack(side="left", padx=20, pady=10)

        self.speed_label = ctk.CTkLabel(
            temp_frame, text="Fan: --%", font=("", 14, "bold")
        )
        self.speed_label.pack(side="left", padx=20, pady=10)

        # Manual speed slider
        self.slider_frame = ctk.CTkFrame(self)
        self.slider_frame.grid(row=3, column=0, padx=10, pady=5, sticky="ew")

        slider_label = ctk.CTkLabel(self.slider_frame, text="Manual Speed:", font=("", 12))
        slider_label.pack(pady=5)

        self.speed_slider = ctk.CTkSlider(
            self.slider_frame, from_=0, to=100,
            number_of_steps=100,
            command=self._on_slider_change
        )
        self.speed_slider.set(0)
        self.speed_slider.pack(fill="x", padx=20, pady=5)

        self.slider_value_label = ctk.CTkLabel(
            self.slider_frame, text="0%", font=("", 12)
        )
        self.slider_value_label.pack(pady=5)

        # Initially hide slider in auto mode
        self._update_slider_visibility()

        # Exit button
        exit_btn = ctk.CTkButton(
            self, text="Exit", command=self._on_exit_click,
            fg_color="red", hover_color="darkred"
        )
        exit_btn.grid(row=4, column=0, padx=10, pady=20)

    def _setup_callbacks(self):
        """Setup controller callbacks."""
        self.controller.set_callbacks(
            on_status_change=self._on_status_change,
            on_temps_change=self._on_temps_change,
            on_speed_change=self._on_speed_change,
        )

    def _on_mode_change(self):
        """Handle mode change."""
        mode = Mode(self.mode_var.get())
        self.controller.mode = mode
        self._update_slider_visibility()

        # If switching to manual, set initial speed to current fan speed
        if mode == Mode.MANUAL:
            current_speed = self.controller.current_speed
            self.controller.manual_speed = current_speed
            self.speed_slider.set(current_speed)
            self.slider_value_label.configure(text=f"{current_speed}%")

    def _on_slider_change(self, value):
        """Handle slider value change."""
        speed = int(value)
        self.slider_value_label.configure(text=f"{speed}%")

        if self.controller.mode == Mode.MANUAL:
            self.controller.manual_speed = speed

    def _on_status_change(self, connected: bool, running: bool):
        """Handle status change from controller."""
        status_text = "Connected" if connected else "Disconnected"
        if running:
            status_text += f" ({self.controller.mode.name})"
        if connected and self.controller.port:
            status_text += f" - {self.controller.port}"
        self.status_label.configure(text=f"Status: {status_text}")

        # Update tray icon based on connection status
        if self.gui_app:
            self.gui_app._update_tray_icon(connected)

    def _on_temps_change(self, cpu: int, gpu: int):
        """Handle temperature change from controller."""
        self.cpu_label.configure(text=f"CPU: {cpu}°C")
        self.gpu_label.configure(text=f"GPU: {gpu}°C")

    def _on_speed_change(self, speed: int):
        """Handle speed change from controller."""
        self.speed_label.configure(text=f"Fan: {speed}%")

        # Update slider in manual mode without triggering callback
        if self.controller.mode == Mode.MANUAL:
            self.speed_slider.set(speed)
            self.slider_value_label.configure(text=f"{speed}%")

    def _update_slider_visibility(self):
        """Show/hide slider based on mode."""
        if self.controller.mode == Mode.MANUAL:
            self.slider_frame.grid()
        else:
            self.slider_frame.grid_remove()

    def _on_exit_click(self):
        """Handle exit button click."""
        self.on_exit()


class GUIApp:
    """Main application with tray icon and GUI window."""

    window = ctk.CTk
    def __init__(self):
        self.controller = SpeedController()
        self.window: Optional[ctk.CTk] = None
        self.control_panel: Optional[ControlWindow] = None
        self.tray_icon: Optional[pystray.Icon] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

        # Load icons
        self.icon_path = Path(__file__).parent.parent.parent.parent / "media" / "icon.ico"
        self.icon_red_path = Path(__file__).parent.parent.parent.parent / "media" / "icon-red.ico"
        self.icon_image = self._load_icon(self.icon_path)
        self.icon_red_image = self._load_icon(self.icon_red_path)
        self._is_connected = True  # Track connection status for icon

    def _load_icon(self, icon_path: Path) -> Optional[Image.Image]:
        """Load the application icon."""
        try:
            if icon_path.exists():
                return Image.open(icon_path)
            else:
                # Create a simple default icon
                return Image.new('RGB', (64, 64), color='blue')
        except Exception as e:
            logging.error(f"Failed to load icon: {e}")
            return Image.new('RGB', (64, 64), color='blue')

    def _create_tray_menu(self) -> list:
        """Create the tray menu items."""
        return [
            Item("Show", self._show_window, default=True),
            Item("Start", self._start_control),
            Item("Stop", self._stop_control),
            Item("Exit", self._exit_app),
        ]

    def _create_tray_icon(self) -> pystray.Icon:
        """Create the system tray icon."""
        menu = pystray.Menu(*self._create_tray_menu())
        icon = pystray.Icon(
            APP_NAME,
            self.icon_image,
            APP_NAME,
            menu
        )
        return icon

    def _update_tray_icon(self, connected: bool):
        """Update the tray icon based on connection status."""
        if connected == self._is_connected:
            return  # No change needed

        self._is_connected = connected

        # Select the appropriate icon
        icon_image = self.icon_red_image if not connected else self.icon_image

        # Stop and recreate the tray icon with new image
        if self.tray_icon:
            self.tray_icon.icon = icon_image

    def _create_window(self):
        """Create the GUI window."""
        self.window = ctk.CTk()
        self.window.title(APP_NAME)
        self.window.geometry("350x350")
        self.window.resizable(False, False)

        # Set window icon
        if self.icon_path.exists():
            self.window.iconbitmap(str(self.icon_path))

        # Create control panel
        self.control_panel = ControlWindow(
            self.window, self.controller, self._exit_app, self
        )
        self.control_panel.pack(fill="both", expand=True)

        # Handle window close (X button) - exits app
        self.window.protocol("WM_DELETE_WINDOW", self._exit_app)

        # Handle minimize - minimize to tray
        self.window.bind("<Unmap>", self._on_minimize)

        # Load window position
        self._load_window_position()

    def _on_minimize(self, event):
        """Handle window minimize - hide to tray."""
        if self.window and self.window.state() == "iconic":
            # Window is minimized, hide it to tray
            self.window.after(10, self._hide_window)

    def _load_window_position(self):
        """Load saved window position."""
        if not self.window:
            return
        try:
            import json
            config_path = Path.home() / ".iets-speed-control" / "config.json"
            if config_path.exists():
                with open(config_path) as f:
                    config = json.load(f)
                    x = config.get("window_x", 100)
                    y = config.get("window_y", 100)
                    self.window.geometry(f"+{x}+{y}")
        except Exception as e:
            logging.debug(f"Could not load window position: {e}")

    def _save_window_position(self):
        """Save window position."""
        if not self.window:
            return
        try:
            import json
            config_path = Path.home() / ".iets-speed-control" / "config.json"
            config_path.parent.mkdir(exist_ok=True)

            geometry = self.window.geometry()
            # Parse geometry string: WxH+X+Y
            parts = geometry.split("+")
            if len(parts) == 3:
                x, y = int(parts[1]), int(parts[2])
                config = {"window_x": x, "window_y": y}
                with open(config_path, "w") as f:
                    json.dump(config, f)
        except Exception as e:
            logging.debug(f"Could not save window position: {e}")

    def _show_window(self, icon=None, item=None):
        """Show the GUI window."""
        if self.window:
            self.window.after(0, self.window.deiconify)
            self.window.after(0, self.window.state, "normal")
            self.window.after(0, self.window.lift)
            self.window.after(0, self.window.focus_force)

    def _hide_window(self):
        """Hide the GUI window."""
        if self.window:
            self._save_window_position()
            self.window.withdraw()

    def _start_control(self, icon=None, item=None):
        """Start the control loop."""
        if self.loop and not self.controller.running:
            asyncio.run_coroutine_threadsafe(
                self.controller.start(), self.loop
            )

    def _stop_control(self, icon=None, item=None):
        """Stop the control loop."""
        if self.loop and self.controller.running:
            asyncio.run_coroutine_threadsafe(
                self.controller.stop(), self.loop
            )

    def _exit_app(self, icon=None, item=None):
        """Exit the application."""
        self._running = False

        # Stop controller synchronously
        if self.loop and self.controller.running:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.stop(), self.loop
            )
            try:
                future.result(timeout=2.0)
            except Exception as e:
                logging.debug(f"Error stopping controller: {e}")

        # Save window position
        if self.window:
            self._save_window_position()
            self.window.after(0, self.window.destroy)

        # Stop tray icon
        if self.tray_icon:
            self.tray_icon.stop()

        # Stop the event loop
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _update_tray_tooltip(self):
        """Update tray icon tooltip with current status."""
        if self.tray_icon:
            status = f"CPU: {self.controller.cpu_temp}°C | GPU: {self.controller.gpu_temp}°C | Fan: {self.controller.current_speed}%"
            self.tray_icon.title = status

    def _run_async_loop(self):
        """Run the asyncio event loop in a separate thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Start controller
        self.loop.create_task(self.controller.start())

        # Run event loop
        self.loop.run_forever()

    def run(self):
        """Run the application."""
        self._running = True

        # Create tray icon
        self.tray_icon = self._create_tray_icon()

        # Create GUI window
        self._create_window()

        # Start async loop in separate thread
        async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        async_thread.start()

        # Setup periodic tooltip update
        def update_tooltip():
            if self._running and self.window:
                self._update_tray_tooltip()
                self.window.after(1000, update_tooltip)

        if self.window:
            self.window.after(1000, update_tooltip)

        # Run tray icon in separate thread
        tray_thread = threading.Thread(
            target=self.tray_icon.run_detached,
            daemon=True
        )
        tray_thread.start()

        # Run GUI main loop (blocks)
        if self.window:
            self.window.iconify()
            self.window.mainloop()

        # Cleanup
        self._running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


def gui():
    """Main entrypoint."""
    logging.basicConfig(
        level=logging.INFO if not env.VERBOSE else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    app = GUIApp()
    app.run()


if __name__ == "__main__":
    gui()
