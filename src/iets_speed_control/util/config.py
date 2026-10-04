"""Configuration, loaded from ~/.iets-speed-control/config.yaml.

Two properties shape this module:

* **Only non-default values are written.** The file stays as short as what the user actually
  changed, so it reads as a list of decisions rather than a dump of every knob.
* **Comments survive.** Saving round-trips the existing document with ruamel, so hand-written
  comments on keys that are still present are kept.

The schema is documented in CONFIG.md.
"""

import logging
import re
from dataclasses import dataclass, field, fields, is_dataclass, replace
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedSeq

from .filters import DEFAULT_FILTERS, compile_filter
from .source_names import canonical_source

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".iets-speed-control"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# Kept here rather than imported from controller.Mode, which imports this module. A test asserts the
# two lists agree, so adding a mode to the enum cannot silently leave this behind.
CONTROL_MODES = ("auto", "manual")
DEFAULT_CURVE = [[40, 0], [55, 20], [70, 50], [85, 75], [95, 100]]


class ConfigError(Exception):
    """Raised when the configuration file exists but cannot be used."""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str | None = None  # None -> no log file at all; set a path to turn one on


@dataclass
class DeviceConfig:
    port: str = "COM7"
    name: str = "USB-Enhanced-SERIAL CH9102"
    serial: str | None = None
    baudrate: int = 115200
    timeout: float = 0.3
    pwm_command: str = "Dimmer"


@dataclass
class LhmWebConfig:
    url: str = "http://localhost:8085/data.json"
    timeout: float = 1.0
    username: str = ""
    password: str = ""


@dataclass
class SensorsConfig:
    provider: str = "aida64"
    # The filters of each source, by its code name. A source without an entry uses DEFAULT_FILTERS, so
    # the file only lists the sources whose filters were changed.
    filters: dict[str, list[str]] = field(default_factory=dict)
    lhm_web: LhmWebConfig = field(default_factory=LhmWebConfig)


@dataclass
class ControlConfig:
    delay: float = 1.1
    temp_window: int = 5
    resync_every: int = 30
    max_step: int = 100  # limits LOWERING the PWM only; spin-up is deliberately unlimited
    ignore_less_than: int = 0
    mode: str = "auto"  # remembered between runs, along with the speed Manual mode uses
    manual_speed: int = 0
    curve: list = field(default_factory=lambda: [list(point) for point in DEFAULT_CURVE])


@dataclass
class UiConfig:
    history_window: int = 600
    rail_collapsed: bool = False
    minimize_on_launch: bool = True
    hide_to_tray_on_minimize: bool = True
    window_x: int | None = None
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None


@dataclass
class Config:
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    ui: UiConfig = field(default_factory=UiConfig)


# --- reading ----------------------------------------------------------------------------------


def _yaml() -> YAML:
    parser = YAML()
    parser.preserve_quotes = True
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def _apply(section, values, path: str):
    """Copy known keys from a mapping onto a dataclass, recursing into nested sections."""
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            logger.warning(f"Ignoring unknown configuration key {path}{key}")
            continue

        current = getattr(section, key)
        if is_dataclass(current) and isinstance(value, dict):
            _apply(current, value, f"{path}{key}.")
        else:
            setattr(section, key, value)


def load(path: Path | None = None) -> Config:
    """Read the configuration, falling back to defaults for anything absent."""
    target = Path(path) if path else CONFIG_PATH
    config = Config()

    if not target.exists():
        logger.debug(f"No configuration at {target}; using defaults")
        return validate(config)

    try:
        raw = _yaml().load(target.read_text(encoding="utf-8"))
    except (YAMLError, OSError) as e:
        raise ConfigError(f"Cannot read {target}: {e}") from e

    if raw is None:
        return validate(config)

    if not isinstance(raw, dict):
        raise ConfigError(f"{target} must contain a mapping of sections, got {type(raw).__name__}")

    legacy = _pop_legacy_filters(raw)
    _apply(config, raw, "")
    if legacy:
        _migrate_legacy_filters(config, legacy)
    return validate(config)


# Until the filter list, a file named one CPU and one GPU filter, matched as plain text, with these
# defaults for a key it left out.
LEGACY_FILTER_KEYS = {"cpu_filter": "CPU", "gpu_filter": "GPU"}


def _pop_legacy_filters(raw: dict) -> list[str]:
    sensors = raw.get("sensors")
    if not isinstance(sensors, dict) or not any(key in sensors for key in LEGACY_FILTER_KEYS):
        return []

    return [str(sensors.pop(key, default)) for key, default in LEGACY_FILTER_KEYS.items()]


def _migrate_legacy_filters(config: Config, legacy: list[str]):
    """Carry sensors.cpu_filter and sensors.gpu_filter over as the current source's filters.

    They were plain text, so they are escaped: "CPU (Tctl)" keeps matching that text rather than turning
    into a regex group. Defaults are not carried over; the source's default list already holds them.
    """
    if legacy == list(DEFAULT_FILTERS):
        return

    # Keyed by the code name, as every reader looks it up: "ohm" in the file means lhm.
    provider = canonical_source(config.sensors.provider)
    if provider in config.sensors.filters:
        logger.warning(f"Ignoring {', '.join(LEGACY_FILTER_KEYS)}: sensors.filters.{provider} is already set")
        return

    patterns = list(dict.fromkeys(re.escape(text) for text in legacy))
    config.sensors.filters[provider] = patterns
    logger.info(f"Moved {', '.join(LEGACY_FILTER_KEYS)} into sensors.filters.{provider}: {patterns}")


def filters_for(config: Config, provider: str) -> list[str]:
    """The filters of one source, falling back to the defaults when its list was never changed."""
    return list(config.sensors.filters.get(provider, DEFAULT_FILTERS))


# --- validation -------------------------------------------------------------------------------


def validate(config: Config) -> Config:
    """Reject values that would break the control loop, with a message naming the key."""
    if config.logging.level.upper() not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {', '.join(LOG_LEVELS)}, got {config.logging.level!r}")
    config.logging.level = config.logging.level.upper()

    # A digits-only serial would otherwise load as an int, and matching does a substring test.
    if config.device.serial is not None:
        config.device.serial = str(config.device.serial)

    for key, value in (("device.baudrate", config.device.baudrate), ("device.timeout", config.device.timeout)):
        if value <= 0:
            raise ConfigError(f"{key} must be greater than zero, got {value!r}")

    if config.control.delay <= 0:
        raise ConfigError(f"control.delay must be greater than zero, got {config.control.delay!r}")
    if config.control.temp_window < 1:
        raise ConfigError(f"control.temp_window must be at least 1, got {config.control.temp_window!r}")
    for key, value in (
        ("control.resync_every", config.control.resync_every),
        ("control.max_step", config.control.max_step),
        ("control.ignore_less_than", config.control.ignore_less_than),
    ):
        if value < 0:
            raise ConfigError(f"{key} must not be negative, got {value!r}")

    if str(config.control.mode).strip().lower() not in CONTROL_MODES:
        raise ConfigError(f"control.mode must be one of {', '.join(CONTROL_MODES)}, got {config.control.mode!r}")
    config.control.mode = str(config.control.mode).strip().lower()

    if not 0 <= config.control.manual_speed <= 100:
        raise ConfigError(f"control.manual_speed must be within 0-100, got {config.control.manual_speed!r}")

    config.control.curve = _validated_curve(config.control.curve)

    if not isinstance(config.sensors.provider, str) or not config.sensors.provider.strip():
        raise ConfigError(f"sensors.provider must be the name of a source, got {config.sensors.provider!r}")

    config.sensors.filters = _validated_filters(config.sensors.filters)

    if config.sensors.lhm_web.timeout <= 0:
        raise ConfigError(f"sensors.lhm_web.timeout must be greater than zero, got {config.sensors.lhm_web.timeout!r}")

    if config.ui.history_window <= 0:
        raise ConfigError(f"ui.history_window must be greater than zero, got {config.ui.history_window!r}")

    # No upper or lower bound beyond this: the window's own minimum size clamps a too-small value,
    # and a position off the current screen is normal for a monitor that is temporarily unplugged.
    for key, value in (("ui.window_width", config.ui.window_width), ("ui.window_height", config.ui.window_height)):
        if value is not None and value <= 0:
            raise ConfigError(f"{key} must be greater than zero, got {value!r}")

    return config


def _validated_filters(filters) -> dict:
    if not isinstance(filters, dict):
        raise ConfigError(f"sensors.filters must map a source to its list of filters, got {filters!r}")

    result = {}
    for source, patterns in filters.items():
        key = f"sensors.filters.{source}"
        if not isinstance(patterns, list) or not patterns:
            # An empty list would read 0 °C and hold the fan at the curve's floor.
            raise ConfigError(f"{key} needs at least one filter, got {patterns!r}")

        for index, pattern in enumerate(patterns):
            try:
                compile_filter(pattern)
            except ValueError as e:
                raise ConfigError(f"{key}[{index}]: {e}") from None
            if patterns.index(pattern) != index:
                # A repeat selects nothing new, and two equal lines could not say which one holds the maximum.
                raise ConfigError(f"{key}[{index}]: {pattern!r} is already in the list")

        result[str(source)] = [str(pattern) for pattern in patterns]

    return result


def _validated_curve(curve) -> list:
    if not isinstance(curve, list) or len(curve) < 2:
        raise ConfigError("control.curve needs at least two [temperature, percent] points")

    points = []
    for index, point in enumerate(curve):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ConfigError(f"control.curve[{index}] must be [temperature, percent], got {point!r}")

        temperature, percent = point
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point):
            raise ConfigError(f"control.curve[{index}] must hold numbers, got {point!r}")
        if not 0 <= percent <= 100:
            raise ConfigError(f"control.curve[{index}] percent {percent} is outside 0-100")
        points.append([temperature, percent])

    temperatures = [temperature for temperature, _percent in points]
    if temperatures != sorted(temperatures) or len(set(temperatures)) != len(temperatures):
        raise ConfigError(f"control.curve temperatures must strictly increase, got {temperatures}")

    percents = [percent for _temperature, percent in points]
    if percents != sorted(percents):
        # Allowed: the fan simply follows it. Worth saying out loud, because the editor never
        # produces this and it usually means a hand-edit went wrong.
        logger.warning(f"control.curve percentages decrease somewhere: {percents}")

    return points


# --- writing ----------------------------------------------------------------------------------


def to_sparse(config: Config, reference: Config | None = None) -> dict:
    """The subset of `config` that differs from the defaults, as nested plain dicts."""
    reference = reference or Config()
    result = {}
    for f in fields(config):
        value = getattr(config, f.name)
        default = getattr(reference, f.name)
        if is_dataclass(value):
            nested = to_sparse(value, default)
            if nested:
                result[f.name] = nested
        elif value != default:
            result[f.name] = value
    return result


def _tidy(value):
    """Write 70 rather than 70.0: the editor works in floats, the file should stay readable."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _as_flow(points) -> CommentedSeq:
    """Render curve points as `- [70, 50]` instead of a nested block sequence per point."""
    outer = CommentedSeq()
    for point in points:
        inner = CommentedSeq(_tidy(value) for value in point)
        inner.fa.set_flow_style()
        outer.append(inner)
    return outer


def _merge_into(document, sparse: dict):
    """Update a round-tripped mapping in place so comments on surviving keys are kept."""
    for key in list(document.keys()):
        if key not in sparse:
            del document[key]  # back to its default: drop it rather than write the default out

    for key, value in sparse.items():
        if isinstance(value, dict) and isinstance(document.get(key), dict):
            _merge_into(document[key], value)
        else:
            document[key] = value


def save(config: Config, path: Path | None = None) -> Path:
    """Write only what differs from the defaults, keeping existing comments.

    An unchanged file is left alone entirely. The window geometry is saved on every minimise and on
    exit, and most of those save exactly what is already on disk; rewriting it each time would spend
    flash write cycles to change nothing.
    """
    target = Path(path) if path else CONFIG_PATH
    sparse = to_sparse(config)
    if "curve" in sparse.get("control", {}):
        sparse["control"]["curve"] = _as_flow(sparse["control"]["curve"])

    parser = _yaml()
    document = None
    existing = None
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
            document = parser.load(existing)
        except (YAMLError, OSError) as e:
            logger.warning(f"Rewriting {target} from scratch: {e}")

    if isinstance(document, dict):
        _merge_into(document, sparse)
    else:
        document = sparse

    buffer = StringIO()
    parser.dump(document, buffer)
    rendered = buffer.getvalue()

    if rendered == existing:
        logger.debug(f"{target} is already up to date; not rewriting it")
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")

    logger.debug(f"Saved {len(sparse)} changed section(s) to {target}")
    return target


def defaults() -> Config:
    return Config()


def copy(config: Config) -> Config:
    """A detached copy, for offering Reset without mutating the live configuration."""
    return replace(
        config,
        logging=replace(config.logging),
        device=replace(config.device),
        sensors=replace(
            config.sensors,
            filters={source: list(patterns) for source, patterns in config.sensors.filters.items()},
            lhm_web=replace(config.sensors.lhm_web),
        ),
        control=replace(config.control, curve=[list(point) for point in config.control.curve]),
        ui=replace(config.ui),
    )


# --- access by dotted path --------------------------------------------------------------------
#
# The settings panel edits twenty-odd keys spread over five sections. Addressing them as
# "control.delay" keeps a row ignorant of which dataclass it writes to, and makes it possible to
# validate one edit on a throwaway copy before letting it near the live configuration.


def get_value(config: Config, path: str):
    """Read `config` at a dotted path, e.g. "sensors.lhm_web.url"."""
    target = config
    for part in path.split("."):
        target = getattr(target, part)
    return target


def set_value(config: Config, path: str, value):
    """Write `config` at a dotted path."""
    *parents, leaf = path.split(".")
    target = config
    for part in parents:
        target = getattr(target, part)
    if not hasattr(target, leaf):
        raise AttributeError(f"No configuration key {path}")

    setattr(target, leaf, value)


def with_value(config: Config, path: str, value) -> Config:
    """A validated copy of `config` with one key changed.

    Raises ConfigError when the value is unusable, so a bad edit never reaches the live
    configuration. The returned copy holds the normalised value ("debug" comes back as "DEBUG"),
    which is what the caller should write back.
    """
    candidate = copy(config)
    set_value(candidate, path, value)
    return validate(candidate)


try:
    CONFIG = load()
except ConfigError as e:
    # Falling back to defaults could silently under-cool the machine, so refuse to start instead.
    logging.getLogger(__name__).error(f"Invalid configuration: {e}")
    raise SystemExit(1) from e
