Project Overview: IETS Speed Control

Important
- use astral uv where possible

Summary
- Purpose: Control the PWM (fan speed) of a 12V IETS laptop cooler stand using a serially connected microcontroller (e.g., ESP32) based on your Windows CPU/GPU temperatures.
- Platform: Windows.
- Data source: AIDA64 (via WMI) for CPU/GPU temperature readings.
- Action: Computes a target value from temperatures and sends a serial command like "Dimmer {value}" to the device.

How It Works (High Level)
1) Read sensors: The app queries AIDA64 for CPU and GPU temperatures.
2) Select target: It filters relevant sensors and takes the maximum temperature as the control input.
3) Compute output: It maps that temperature to a PWM percentage/value.
4) Send command: It sends a serial command (by default: Dimmer {value}) to the microcontroller via the selected COM port.

Key Components (Code Structure)
- iets_speed_control/entities
  - serial_device.py: Async serial I/O wrapper used to connect, read, and write commands to the device.
  - dimmer.py: Specialization for PWM control; exposes read_dimmer_value and set_dimmer_value.
- iets_speed_control/util
  - env.py: Configuration defaults (e.g., default COM port, baud rate, PWM command name).
  - sensors.py: AIDA64/WMI temperature access and sensor utilities.
  - logger.py, tools.py: Logging and helper utilities.
- iets_speed_control/entrypoints
  - cli.py: Console entry point (project script name: iets-speed-control).
  - gui.pyw: GUI entry point (project GUI script name: iets-speed-control-gui).

Configuration
- Create a .env file (see .env.example referenced in README). Typical settings include:
  - Serial port and baud rate.
  - PWM command name (e.g., "Dimmer").
  - Any thresholds/mapping parameters if exposed.
- Ensure AIDA64 is installed and configured:
  - Preferences → External Applications → enable writing sensors to WMI.
  - Keep AIDA64 running while using this app.

Running the App
- GUI
  - Use pre-built binaries if available or build via build.cmd, then run the GUI (uv run iets-speed-control-gui).
- Console (CLI)
  - Use run.cmd, or run the installed script iets-speed-control, or `uv run iets_speed_control` if developing locally.

Requirements
- Python Latest (per pyproject.toml).
- Key dependencies: aioserial, pyserial, python-dotenv, wmi, wxasync.

Repository Pointers
- README.md: Setup instructions, screenshots, and quick how-to.
- docs/ESP32_Tasmota.md: Example for preparing an ESP32 device.
- media/icon.*: App icons.
- build.cmd / run.cmd: Convenience scripts for building and running.

Notes
- The serial command label and mapping can be tuned via env/config. Default PWM command is defined in util/env.py as PWM_COMMAND.
- On Windows, confirm the correct COM port for your device and that no other program is blocking it.

Support / Development
- Use the CLI for quick testing and the GUI for interactive control.
- Check iets_speed_control/util/logger.py for log settings when diagnosing serial or sensor issues.
