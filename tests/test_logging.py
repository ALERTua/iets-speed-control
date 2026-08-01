"""Guards on the logging setup itself.

The rules being protected: library modules never configure logging, the entrypoints configure it
once, VERBOSE/LOG_LEVEL resolve to the level they claim, third-party loggers stay quiet, and
records still reach pytest's caplog.
"""

import ast
import logging
from pathlib import Path

import pytest

from iets_speed_control.util import logger as logger_module

PACKAGE_ROOT = Path(logger_module.__file__).parent.parent
ENTRYPOINTS = PACKAGE_ROOT / "entrypoints"


@pytest.fixture
def logging_state():
    """Restore the global logging configuration after a test has reconfigured it."""
    root = logging.getLogger()
    package = logging.getLogger(logger_module.PACKAGE_LOGGER)
    saved = (root.handlers[:], root.level, package.level, logger_module._configured)
    try:
        yield
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        package.setLevel(saved[2])
        logger_module._configured = saved[3]


def source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py")) + sorted(PACKAGE_ROOT.rglob("*.pyw"))


def test_no_module_calls_basic_config():
    """basicConfig anywhere in the package would re-introduce competing configuration."""
    offenders = [path for path in source_files() if "logging.basicConfig" in path.read_text(encoding="utf-8")]

    assert not offenders, f"logging.basicConfig must not be used: {[p.name for p in offenders]}"


def test_only_entrypoints_configure_logging():
    offenders = [
        path
        for path in source_files()
        if "configure_logging(" in path.read_text(encoding="utf-8")
        and path.parent != ENTRYPOINTS
        and path != Path(logger_module.__file__)
    ]

    assert not offenders, f"only entrypoints may configure logging: {[p.name for p in offenders]}"


def test_no_module_logs_through_the_root_logger():
    """Direct logging.info/error/... calls bypass the per-module logger namespace."""
    offenders = []
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue

            target = node.func
            if (
                isinstance(target.value, ast.Name)
                and target.value.id == "logging"
                and target.attr in {"debug", "info", "warning", "error", "exception", "critical", "log"}
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, f"use a module logger instead of the root logger: {offenders}"


@pytest.mark.parametrize(
    ("log_level", "expected"),
    [
        ("INFO", logging.INFO),
        ("DEBUG", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("warning", logging.WARNING),  # matched case-insensitively
        ("nonsense", logging.INFO),  # unknown values fall back instead of crashing
    ],
)
def test_level_resolution(monkeypatch, log_level, expected):
    from iets_speed_control.util.config import CONFIG

    monkeypatch.setattr(CONFIG.logging, "level", log_level)

    resolved, complaint = logger_module.resolve_level()

    assert resolved == expected
    assert (complaint is not None) == (log_level == "nonsense")


def test_default_level_is_info_not_debug(tmp_path, logging_state, monkeypatch):
    """Regression: the old VERBOSE handling treated the string "0" as truthy and forced DEBUG."""
    from iets_speed_control.util.config import CONFIG

    monkeypatch.setattr(CONFIG.logging, "level", "INFO")

    logger_module.configure_logging(log_file=tmp_path / "log.txt", force=True)

    package = logging.getLogger(logger_module.PACKAGE_LOGGER)
    assert package.level == logging.INFO
    assert not package.isEnabledFor(logging.DEBUG)


def test_package_logger_name_matches_the_import_path():
    """Regression: a hard-coded name silenced every log when run as `-m src.iets_speed_control...`.

    README documents that invocation for the GUI, which makes the loggers "src.iets_speed_control.*".
    The package logger name must follow the actual import path, whatever it is.
    """
    assert logger_module.PACKAGE_LOGGER == logger_module.__name__.rsplit(".", 2)[0]
    assert logger_module.__name__.startswith(f"{logger_module.PACKAGE_LOGGER}.")


def test_third_party_loggers_stay_quiet(tmp_path, logging_state):
    """asyncio and wmi must not inherit our verbosity, or they drown the output."""
    logger_module.configure_logging(level="DEBUG", log_file=tmp_path / "log.txt", force=True)

    assert logging.getLogger(logger_module.PACKAGE_LOGGER).isEnabledFor(logging.DEBUG)
    for noisy in ("asyncio", "wmi", "comtypes", "PIL"):
        effective = logging.getLogger(noisy).getEffectiveLevel()
        assert effective == logging.WARNING, f"{noisy} should inherit WARNING, got {logging.getLevelName(effective)}"


def test_configure_logging_is_idempotent(tmp_path, logging_state):
    logger_module.configure_logging(log_file=tmp_path / "log.txt", force=True)
    first = len(logging.getLogger().handlers)

    logger_module.configure_logging(log_file=tmp_path / "log.txt")
    logger_module.configure_logging(log_file=tmp_path / "log.txt")

    assert len(logging.getLogger().handlers) == first


def test_records_propagate_to_root_handlers(tmp_path, logging_state):
    """propagate must stay on, or every caplog-based test silently stops asserting anything.

    Checked with an explicit handler rather than caplog, because dictConfig replaces the root
    handler list and would evict pytest's capture handler -- which is also why a test that needs
    caplog must not call configure_logging (see test_lhm_web_auth.py).
    """
    logger_module.configure_logging(level="DEBUG", log_file=tmp_path / "log.txt", force=True)

    package = logging.getLogger(logger_module.PACKAGE_LOGGER)
    assert package.propagate, "the package logger must keep propagating to the root handlers"

    captured: list[str] = []

    class Collector(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    collector = Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    try:
        logging.getLogger(f"{logger_module.PACKAGE_LOGGER}.sensors.probe").info("propagated")
    finally:
        root.removeHandler(collector)

    assert "propagated" in captured


def test_no_log_file_is_written_by_default(tmp_path, logging_state, monkeypatch):
    """Nothing lands on the user's disk unless they ask for it: logging.file is opt-in."""
    from iets_speed_control.util.config import CONFIG

    monkeypatch.setattr(CONFIG.logging, "file", None)
    suggested = tmp_path / "logs" / "iets-speed-control.log"
    monkeypatch.setattr(logger_module, "default_log_file", lambda: suggested)

    logger_module.configure_logging(level="INFO", force=True)
    logging.getLogger(f"{logger_module.PACKAGE_LOGGER}.test").info("not written anywhere")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert not any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)
    assert not suggested.exists(), "not even the folder should be created"
    assert not suggested.parent.exists()


def test_a_configured_path_turns_the_file_on(tmp_path, logging_state, monkeypatch):
    from iets_speed_control.util.config import CONFIG

    target = tmp_path / "asked" / "for.log"
    monkeypatch.setattr(CONFIG.logging, "file", str(target))

    logger_module.configure_logging(level="INFO", force=True)
    logging.getLogger(f"{logger_module.PACKAGE_LOGGER}.test").info("asked for it")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert target.exists()
    assert "asked for it" in target.read_text(encoding="utf-8")


def test_writes_to_the_log_file(tmp_path, logging_state):
    """An explicit path still works: this is the entrypoint's --log-file argument."""
    target = tmp_path / "nested" / "log.txt"

    logger_module.configure_logging(level="INFO", log_file=target, force=True)
    logging.getLogger(f"{logger_module.PACKAGE_LOGGER}.test").info("written to file")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert target.exists()
    assert "written to file" in target.read_text(encoding="utf-8")


def test_unwritable_log_file_falls_back_to_console(tmp_path, logging_state, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(Path, "mkdir", refuse)

    logger_module.configure_logging(level="INFO", log_file=tmp_path / "denied" / "log.txt", force=True)

    handlers = logging.getLogger().handlers
    assert handlers, "console handler must survive a failed log file"
    assert not any(isinstance(h, logging.FileHandler) for h in handlers)
