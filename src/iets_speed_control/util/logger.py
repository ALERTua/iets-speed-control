"""Logging configuration.

Only entrypoints call `configure_logging()`. Every other module just does
`logger = logging.getLogger(__name__)` and logs to it -- library code must never touch global
logging state.

Handlers live on the root logger while the root *level* stays at WARNING and the package logger
carries the requested level. That keeps third-party loggers (asyncio, wmi, comtypes, PIL) quiet
without setting `propagate = False`, which would hide records from pytest's caplog.
"""

import logging
import logging.config
import os
import sys
from pathlib import Path

# Derived, not hard-coded: running as `-m src.iets_speed_control...` names the loggers
# "src.iets_speed_control.*", and a hard-coded name would silence the whole app in that case.
# This module lives in <package>/util/, so dropping the last part of __package__ gives the root.
PACKAGE_LOGGER = (__package__ or "iets_speed_control").rsplit(".", 1)[0]
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
THIRD_PARTY_LEVEL = "WARNING"
DEFAULT_LEVEL = logging.INFO
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 3

_configured = False


def default_log_file() -> Path:
    """The path offered when the user turns the log file on. Nothing is written unless they do."""
    base = os.getenv("LOCALAPPDATA") or Path.home()
    return Path(base) / "iets-speed-control" / "logs" / "iets-speed-control.log"


def resolve_level(level: str | int | None = None) -> tuple[int, str | None]:
    """Resolve the effective level, returning it plus a complaint about bad input, if any.

    Configuration is validated on load, so a bad name here can only come from an explicit argument.
    """
    from .config import CONFIG

    if isinstance(level, int):
        return level, None

    requested = level if level is not None else CONFIG.logging.level
    named = logging.getLevelNamesMapping().get(str(requested).strip().upper())
    if named is not None:
        return named, None

    return DEFAULT_LEVEL, f"Ignoring unknown log level {requested!r}; using {logging.getLevelName(DEFAULT_LEVEL)}."


def configure_logging(
    level: str | int | None = None,
    log_file: str | Path | None = None,
    force: bool = False,
) -> None:
    """Install console and rotating-file handlers. Safe to call more than once."""
    global _configured
    if _configured and not force:
        return

    from .config import CONFIG

    effective, complaint = resolve_level(level)
    handlers: dict[str, dict] = {}

    # Launched without a console (pythonw, or the GUI exe from a shortcut) there is no stderr at all,
    # so do not install a handler that writes nowhere.
    if sys.stderr is not None:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stderr",
        }

    # No file unless one is asked for. Writing a log nobody reads is a background side effect on the
    # user's disk, so it is opt-in: set logging.file to a path. Launched from a shortcut the GUI has no
    # console to print to either, so that combination leaves no trace at all -- the trade for not
    # writing by default.
    configured = log_file or CONFIG.logging.file
    file_error = None
    if configured:
        target = Path(configured)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handlers["file"] = {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "filename": str(target),
                "maxBytes": MAX_BYTES,
                "backupCount": BACKUP_COUNT,
                "encoding": "utf-8",
                "delay": True,
            }
        except OSError as e:
            file_error = f"Cannot write the log file at {target}: {e}. Logging to the console only."

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"standard": {"format": LOG_FORMAT}},
            "handlers": handlers,
            # Handlers sit here at NOTSET; this level only gates records logged to root directly,
            # which is what silences third-party loggers that inherit it.
            "root": {"level": THIRD_PARTY_LEVEL, "handlers": list(handlers)},
            "loggers": {PACKAGE_LOGGER: {"level": logging.getLevelName(effective)}},
        }
    )
    logging.captureWarnings(True)
    _configured = True

    logger = logging.getLogger(__name__)
    for problem in (complaint, file_error):
        if problem:
            logger.warning(problem)

    logger.debug(f"Logging at {logging.getLevelName(effective)}; handlers: {', '.join(handlers) or 'none'}")


def reconfigure() -> None:
    """Re-read the logging configuration and rebuild the handlers.

    For the settings panel, where the user can change the level or the log file while the app runs.
    Reconfiguring stays inside this module: callers ask for it by name rather than driving dictConfig
    themselves, so global logging state still has exactly one owner.
    """
    configure_logging(force=True)
