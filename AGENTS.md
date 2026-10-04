# IETS Speed Control — notes for agents

Controls the PWM of a 12 V IETS laptop cooler stand over a serial link to a Tasmota microcontroller,
driven by Windows CPU/GPU temperatures. Windows only. Python 3.14 (`requires-python = "==3.14.*"`).

**Use `uv` for everything.** `uv run <cmd>`, `uv add`, `uv sync --dev`. Never call `pip` or a bare
`python`. Task shortcuts live in the `Justfile` (`just test`, `just test-fast`, `just pre`, `just lint`).

## Layout

| Path | What lives there |
| --- | --- |
| `controller.py` | `SpeedController`: the async control loop, mode, curve, smoothing, reconnect |
| `entities/fan.py` | `FanDevice`, what the controller needs from any fan: connect, `read_speed`, `set_speed` |
| `entities/tasmota_fan.py` | `TasmotaSerialFan`, the one implementation: Tasmota console on a serial port, finds a moved port |
| `entities/serial_device.py` | async serial I/O under it |
| `sensors/` | one module per temperature source, behind the `SensorProvider` protocol |
| `util/config.py` | the whole configuration layer; `CONFIG` is the live object |
| `util/logger.py` | `configure_logging`, `reconfigure`; the only owner of global logging state |
| `util/tools.py` | `MedianSmoother`, `curve_to_ranges`, `calculate_dimmer_value` |
| `util/filters.py` | the filter list: `select` picks the hottest reading the filters match |
| `gui/` | `app` (shell + tray), `status` (Home), `settings`, `filter_list`, `filter_readings`, `curve_editor`, `history`, `nav`, `theme` |
| `entrypoints/` | `cli.py` and `gui.pyw`; the only place allowed to configure logging |

## Configuration

One YAML file at `%USERPROFILE%\.iets-speed-control\config.yaml`. **There is no `.env`** — it was
removed, along with `util/env.py` and `python-dotenv`. Full schema in [CONFIG.md](CONFIG.md).

- `CONFIG` is a module-level object other modules import by reference, so it cannot be rebound —
  mutate its sections. Tests restore it through the autouse `clean_config` fixture.
- Only values differing from the defaults are written, and an unchanged file is not rewritten at all.
- A malformed file raises `SystemExit` on import rather than falling back to defaults: silently
  under-cooling the machine is worse than refusing to start.
- Every key is editable in the GUI. Rows address keys by dotted path and validate on a throwaway copy
  (`with_value`), so a value the app could not start with never reaches `CONFIG`.

## Adding a sensor source

Implement `get_temperatures() -> dict[str, float]` (blocking by contract; the loop calls it through
`asyncio.to_thread`), give the class a `name`, register it in `sensors/__init__.py`, add a raw probe
to `tests/conftest.py` so the e2e tests can compare against an independent answer. Return `{}` when
the source is unavailable rather than raising. Labels should be descriptive and unique — `lhm_web`
builds `"<hardware>/<sensor>"` because bare sensor names are neither.

## Traps that cost real time

- **Tk is not thread-safe.** Controller callbacks arrive on the asyncio thread and must not touch a
  widget: they put plain data on a `queue.Queue` that the Tk thread drains from an `after` job.
  Violating this crashes the process with a Windows access violation after ~15 s, no traceback.
- **Never derive a widget's `wraplength` from the widget it resizes.** Frame and label chase each
  other through `<Configure>` and the window locks up. Row descriptions are kept short instead, and
  `test_no_description_is_cut_off` enforces that they fit at the minimum window size.
- **Grid sizes a card's control column to the widest control in the whole card**, so one wide control
  narrows every description in that card.
- A `CTkLabel` keeps its one-line height: wrapped text simply is not drawn.
- **A geometry request must be realized before the window is iconified**, or Windows discards the
  position (and keeps the size). `_load_window_geometry` calls `update_idletasks`.
- `except A, B:` without parentheses is valid in 3.14 (PEP 758); ruff strips the parens on purpose.
- COM is initialized once per thread and never uninitialized: tearing it down while `wmi` objects are
  still reachable prints "Win32 exception occurred releasing IUnknown".
- `ruff check .` does not scan `.pyw`; `gui.pyw` is covered by pre-commit.
- LibreHardwareMonitor's `/data.json` formats numbers in the system locale (`"63,0 °C"`), reuses
  sensor names, and reports `Distance to TjMax`, which falls as the chip heats. The sources drop it and
  the limits through `sensors.base.is_live_temperature`; the e2e probes keep their own list on purpose.

## Tests

`just test` runs everything the way pre-commit and CI do, `-n 4 --dist loadfile` (~12 s; serial
~16 s). `just test-fast` runs everything that needs no Tk root in parallel (~2 s) — the loop to use
while editing non-GUI code. `just test-e2e` needs AIDA64 or LibreHardwareMonitor actually running.

- The `gui` marker is applied automatically to any test that requests `tk_root`. Parallel runs use
  `--dist loadfile`: each worker builds its own Tk root and module-scoped panels, so a file must stay
  on one worker. `-n auto` on 32 cores with the default distribution measured **3× slower** than
  serial. `test_gui_settings.py` is the longest file (~5 s) and sets the floor of a parallel run.
- **One session-scoped Tk root** (`tk_root`), mapped with `-alpha 0.0`. A withdrawn window receives no
  synthesized events, and creating a second root after the first is destroyed fails outright.
- **Panels are module-scoped and reset per test** (`settings_view` + `view` in `conftest.py`).
  Destroying a `SettingsView` costs ~1 s inside Tcl for its 467 widgets; rebuilding it per test cost
  the suite over a minute. Anything a test can change must be undone in `reset_settings_view`, or the
  tests quietly become order-dependent.
- Key events go to whatever holds focus: `focus_force()` then `update()` before `event_generate`.
  `CTkEntry.bind` attaches to the inner `tkinter.Entry`, so aim events at `widget._entry`.
- Tk turns a quick second `<Button-1>` into `<Double-Button-1>`, so a test must not rely on which one
  it gets.
- **New tests are verified by mutation**: break the line the test is meant to protect and show that
  this test fails. When clearing the bytecode cache between mutations, delete `__pycache__` —
  Python validates a `.pyc` on (mtime in whole seconds, source size), so two same-size edits in one
  second silently reuse the first one's bytecode and the mutation appears to survive.

## Conventions

- Comments explain *why*, in the register of the surrounding code. No comment restates the code.
- Library code never touches global logging state: `logger = logging.getLogger(__name__)` and nothing
  else. Only `entrypoints/` and `util/logger.py` may configure logging; a test enforces it.
- Line length 120, `ruff format` and `ruff check --fix` (pre-commit runs both plus 11 more hooks).
- Plans for larger changes live in `plans/`, which is gitignored — a local working space.
- Never stage or commit unless explicitly asked.
