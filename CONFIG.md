# Configuration

All settings live in one YAML file:

```
%USERPROFILE%\.iets-speed-control\config.yaml
```

The file is optional. Without it the app runs on the defaults below and creates nothing.

**Only what you changed is written.** Saving from the GUI, or from your own edits, keeps the file to the values that
differ from the defaults — it reads as a list of your decisions, not a dump of every knob. A value you return to its
default is removed from the file rather than written out. Comments you add by hand are preserved on keys that are still
present.

A malformed file stops the app with a message naming the key. That is deliberate: silently falling back to defaults can
leave the fan idling while the chip is hot.

## Editing it

Every key below is editable in the GUI, so hand-editing this file is a convenience rather than a requirement. Each
section here has a matching card in Settings:

| This file                    | Settings section |
|------------------------------|------------------|
| `logging`                    | Logging          |
| `device`                     | Device           |
| `sensors`, `sensors.lhm_web` | Sensors          |
| `control` (tuning)           | Control          |
| `control.mode`, `control.manual_speed` | Manual |
| `control.curve`              | Curve            |
| `ui.history_window`          | Display          |

A value typed in the GUI applies as soon as it validates; a value the app could not start with is refused on the spot,
with the reason under the field, and nothing is written. The one exception is
`device`: the port is opened once, so those keys take effect on **Reconnect**. **Save** in the footer writes the current
state here — until then, edits are in memory only.

## Example

A complete file for a machine that reads temperatures from the LibreHardwareMonitor web server:

```yaml
device:
  serial: 568B022419
  timeout: 0.1

sensors:
  provider: lhm-web

control:
  delay: 0.5
  max_step: 3
  # tuned by hand: near-silent up to 65 °C, full tilt past 92
  curve:
    - [ 1, 48 ]
    - [ 65, 48 ]
    - [ 66, 49 ]
    - [ 85, 54 ]
    - [ 86, 55 ]
    - [ 90, 75 ]
    - [ 92, 100 ]
```

Everything not listed keeps its default.

## `logging`

| Key     | Default | Meaning                                                                                                                                                                                                   |
|---------|---------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `level` | `INFO`  | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Case-insensitive. Third-party libraries stay at `WARNING` regardless, so raising this shows this app's own detail rather than library noise.           |
| `file`  | `null`  | Log file path. `null` writes **no file at all** — nothing lands on disk unless you ask for it. Set a path to turn one on; it rotates at 1 MB and keeps three. Worth doing only for a GUI session you need a record of: launched from a shortcut the GUI has no console to print to, so without a file it leaves no trace. For the console entrypoint, just read the console. |

## `device`

The serial device that drives the fan.

| Key           | Default                      | Meaning                                                                                                       |
|---------------|------------------------------|---------------------------------------------------------------------------------------------------------------|
| `port`        | `COM7`                       | Port tried first.                                                                                             |
| `name`        | `USB-Enhanced-SERIAL CH9102` | Substring matched against the port description when `port` fails.                                             |
| `serial`      | `null`                       | Substring matched against the port serial number. Takes precedence over `name`. Quote it if it is all digits. |
| `baudrate`    | `115200`                     | Must be greater than zero.                                                                                    |
| `timeout`     | `0.3`                        | Read and write timeout in seconds. Must be greater than zero.                                                 |
| `pwm_command` | `Dimmer`                     | Sent as `<command> <value>`, and read back as `<command>` to query.                                           |

## `sensors`

| Key          | Default  | Meaning                                                                                                       |
|--------------|----------|---------------------------------------------------------------------------------------------------------------|
| `provider`   | `aida64` | `aida64`, `lhm` (LibreHardwareMonitor via WMI), `lhm-web` (its web server) or `lenovo-wmi` (Lenovo Legion embedded controller; the app must run as administrator). See README for what each needs. |
| `cpu_filter` | `CPU`    | Substring matched against sensor labels; the CPU temperature is the maximum of the matches.                   |
| `gpu_filter` | `GPU`    | The same for the GPU.                                                                                         |

Labels differ between providers, so a filter tuned for one may match nothing on another — and a filter that matches
nothing yields 0 °C, which quietly drives the fan to its minimum. Open the provider's own window (or `lhm_web.url` in a
browser) to see the exact labels your machine reports.

### `sensors.lhm_web`

Used only when `provider: lhm-web`.

| Key        | Default                           | Meaning                                                                                                                                                                                             |
|------------|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `url`      | `http://localhost:8085/data.json` | Address of LibreHardwareMonitor's Remote Web Server.                                                                                                                                                |
| `timeout`  | `1.0`                             | Request timeout in seconds. Must be greater than zero.                                                                                                                                              |
| `username` | `""`                              | HTTP Basic user, if Remote Web Server → Authentication is on.                                                                                                                                       |
| `password` | `""`                              | HTTP Basic password. **Stored in plain text**, like any password in a config file. Keep the server on `localhost` or a trusted network. Credentials embedded in `url` are stripped from log output. |

## `control`

| Key                | Default   | Meaning                                                                                                                                                                                                                                                                  |
|--------------------|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `delay`            | `1.1`     | Seconds between control ticks. Must be greater than zero.                                                                                                                                                                                                                |
| `temp_window`      | `5`       | Width of the rolling median over temperature readings. `1` disables smoothing. A median rather than an average, so one spurious spike is discarded instead of blended in.                                                                                                |
| `resync_every`     | `30`      | Re-read the PWM value from the device every N ticks, to notice changes made outside this app. `0` never re-reads after the initial connection. The value this app last wrote is otherwise treated as the truth, which keeps one serial round-trip per tick off the wire. |
| `max_step`         | `100`     | Largest single step when **lowering** the PWM. Raising it is deliberately unlimited: the fan should spin up at once when the chip gets hot and coast down gradually.                                                                                                     |
| `ignore_less_than` | `0`       | Ignore computed changes smaller than this many percent.                                                                                                                                                                                                                  |
| `mode`             | `auto`    | `auto` follows the curve, `manual` holds a fixed speed. Remembered between runs. Set from Settings → Manual or from Home.                                                                                                                                                 |
| `manual_speed`     | `0`       | Fan percentage used in Manual mode, 0–100. Remembered between runs, so selecting Manual resumes the speed you last chose rather than whatever the curve is asking for. Set from Settings → Manual.                                                                        |
| `curve`            | see below | Temperature → fan percent.                                                                                                                                                                                                                                               |

### `control.curve`

A list of `[temperature, percent]` points, at least two:

```yaml
control:
  curve:
    - [ 40, 0 ]
    - [ 55, 20 ]
    - [ 70, 50 ]
    - [ 85, 75 ]
    - [ 95, 100 ]
```

- Temperatures must strictly increase. Percentages must be within 0–100.
- Between two points the percentage is interpolated linearly.
- Below the first point and above the last, the nearest percentage is held flat.
- The CPU and GPU temperatures are each mapped through this curve and the higher result wins.
- Percentages that decrease are accepted but logged as a warning: the editor never produces that, so it usually means a
  hand-edit went wrong.

The default is the one shown above. Editing the curve with the mouse in Settings → Curve applies immediately; **Save**
writes it here.

## `ui`

Remembered between runs.

| Key                             | Default | Meaning                                                                                                                                            |
|---------------------------------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| `history_window`                | `600`   | Seconds covered by the temperature graph on Home. Set from Settings → Display.                                                                     |
| `rail_collapsed`                | `false` | Whether the navigation rail starts collapsed to icons.                                                                                             |
| `minimize_on_launch`            | `true`  | Start straight to the tray without showing the window.                                                                                             |
| `hide_to_tray_on_minimize`      | `true`  | Minimising hides the window to the tray. Off leaves it in the taskbar.                                                                             |
| `window_x`, `window_y`          | `null`  | Last window position. Restored only if it is still on the desktop: after a monitor is unplugged the window would otherwise come back out of reach. |
| `window_width`, `window_height` | `null`  | Last window size. Must be greater than zero; a value below the window's minimum size is raised to it.                                              |
