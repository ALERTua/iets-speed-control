[![Stand With Ukraine](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/banner-direct-single.svg)](https://stand-with-ukraine.pp.ua)
[![Made in Ukraine](https://img.shields.io/badge/made_in-Ukraine-ffd700.svg?labelColor=0057b7)](https://stand-with-ukraine.pp.ua)
[![Stand With Ukraine](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/badges/StandWithUkraine.svg)](https://stand-with-ukraine.pp.ua)
[![Russian Warship Go Fuck Yourself](https://raw.githubusercontent.com/vshymanskyy/StandWithUkraine/main/badges/RussianWarship.svg)](https://stand-with-ukraine.pp.ua)

Control the PWM of your laptop cooling pad fan using ESP32 via USB, based on your Windows CPU/GPU temperatures, taken
from AIDA64.

### How it works

- Gets temperatures from AIDA64
- Filters them by CPU and GPU sensors
- Takes the maximum int value among all temperatures
- Sends PWM command (`Dimmer {value}` by default) to the serial device

### Temperature Source

Pick one with `SENSOR_PROVIDER` in `.env`:

| `SENSOR_PROVIDER`  | Source                          | Notes                                                                                               |
|--------------------|---------------------------------|-----------------------------------------------------------------------------------------------------|
| `aida64` (default) | AIDA64 via WMI                  | Paid, must stay running                                                                             |
| `lhm`              | LibreHardwareMonitor via WMI    | Free; falls back to the OpenHardwareMonitor namespace. Not every machine registers the WMI provider |
| `lhm-web`          | LibreHardwareMonitor web server | Free; works where the WMI provider does not                                                         |

Sensor labels differ between sources, so `CPU_SENSOR_FILTER` / `GPU_SENSOR_FILTER` may need adjusting when you switch.

#### AIDA64 Preparation

Getting CPU Temperature appeared to be harder on my Windows 11 i9-13900HX than flashing and connecting ESP32! The only
working way I found was AIDA64 via WMI. If you can get your CPU Temperature easier - good for you!

- Run AIDA64
- In AIDA64 Preferences->External Applications->Enable writing sensors to WMI
- In AIDA64 Preferences->External Applications0>Enable Temperature sensors
- Keep AIDA64 open

<img alt="AIDA64_External_Applications" src="docs/images/AIDA64_External_Applications.png"/>

#### LibreHardwareMonitor Preparation

Common to both providers below:

- Run LibreHardwareMonitor as administrator — reading CPU temperatures needs kernel driver access
- Keep it open

Sensor coverage is per-chip, not universal. LibreHardwareMonitor inherits its hardware support from
OpenHardwareMonitor, whose [documentation](https://openhardwaremonitor.org/documentation/) enumerates the
supported CPU cores, mainboard chips (ITE, Fintek, Nuvoton, Winbond), GPUs, drives and fan controllers one by
one. If your chip is not on that list, no interface will produce a temperature for it — check what the
LibreHardwareMonitor window itself shows before blaming this app.

##### Via WMI (`SENSOR_PROVIDER=lhm`)

Needs nothing beyond running as administrator: LibreHardwareMonitor publishes sensors to WMI on its own, under
the `root\LibreHardwareMonitor` namespace (`root\OpenHardwareMonitor` is tried as a fallback, and the original
interface is described in
[OpenHardwareMonitor-WMI.pdf](http://openhardwaremonitor.org/wordpress/wp-content/uploads/2011/04/OpenHardwareMonitor-WMI.pdf)).

Not every machine gets it. The namespace is registered at runtime, and on some systems it never appears even
with the app elevated and reporting temperatures in its own window — in that case the provider logs
`no temperature sensors found` and you want the web server below instead. Check yours with:

```powershell
Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor | Where-Object SensorType -eq Temperature
```

A list of sensors means the `lhm` provider will work. `The target namespace does not exist` means it will not,
no matter how the app is started.

Sensor labels here are the bare names LibreHardwareMonitor shows, such as `CPU Package` or `GPU Hot Spot`.

##### Via the web server (`SENSOR_PROVIDER=lhm-web`)

Works wherever the app itself works, since it bypasses WMI entirely.

- Enable Options -> Remote Web Server -> Run
- Point `LHM_WEB_URL` at it — default `http://localhost:8085/data.json`
- Raise `LHM_WEB_TIMEOUT` if the machine is slow to answer
- If you turned on Remote Web Server -> Authentication, set `LHM_WEB_USERNAME` and `LHM_WEB_PASSWORD` to match

Authentication is HTTP Basic (LibreHardwareMonitor serves the realm `Libre Hardware Monitor` and answers `401`
on a mismatch). The credentials are sent with the first request rather than after a challenge, and they travel
in plain base64 — keep the server on `localhost` or a trusted network. Wrong credentials are reported once per
poll as `rejected the credentials`, and any `user:password@` embedded in `LHM_WEB_URL` is stripped from the log.

Labels are `<hardware>/<sensor>`, for example `NVIDIA GeForce RTX 4090 Laptop GPU/GPU Hot Spot`. Open
`LHM_WEB_URL` in a browser to see the exact labels your machine reports.

Keep the filters narrow. LibreHardwareMonitor reports two kinds of entry under the same Temperature type that
are not live temperatures: `Distance to TjMax`, which *falls* as the chip heats up, and fixed limits such as
`Critical Temperature` or `Thermal Sensor High Limit`. A filter that catches either drives the fan from the
wrong number — a `Distance to TjMax` of 60 on an idle CPU would spin the fan up for nothing. The defaults
`CPU_SENSOR_FILTER=CPU` and `GPU_SENSOR_FILTER=GPU` avoid both.

### Serial Device Preparation

Example: [ESP32_Tasmota](docs/ESP32_Tasmota.md)

- Connect your Serial Device via USB
- Attach a device pin to the fan PWM
- Attach the device Ground pin to the fan Ground

### Script Preparation

- Create `.env` and fill it using [.env.example](.env.example)

### Script Execution

#### GUI

- Run `uvw run -m src.iets_speed_control.entrypoints.gui` for the GUI mode
- Features: System tray icon, Auto/Manual mode, Manual speed slider
- Minimize to tray, close button exits

#### Console

- Run `uv run iets-speed-control`

### Logs

Both entrypoints log to the console and to a rotating file (1 MB × 3). The file matters most in GUI mode,
which runs under `pythonw` and therefore has no console at all — without it that mode leaves no trace.

- Default location: `%LOCALAPPDATA%\iets-speed-control\logs\iets-speed-control.log`
- Override with `LOG_FILE`; if the path cannot be created the app warns and keeps logging to the console
- Set `LOG_LEVEL` to `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`; `VERBOSE=1` is a shortcut for `DEBUG`
- Third-party libraries (`asyncio`, `wmi`, `comtypes`, `PIL`) stay at `WARNING` regardless, so raising the level
  shows this app's own detail rather than library noise
