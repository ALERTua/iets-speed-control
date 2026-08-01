"""Configuration loading, validation and sparse saving."""

import pytest

from iets_speed_control.util import config as cfg


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config.yaml"


# --- loading ------------------------------------------------------------------------------------


def test_a_missing_file_yields_defaults(path):
    loaded = cfg.load(path)

    assert loaded == cfg.Config()
    assert not path.exists(), "loading must not create the file"


def test_an_empty_file_yields_defaults(path):
    path.write_text("", encoding="utf-8")

    assert cfg.load(path) == cfg.Config()


def test_absent_keys_keep_their_defaults(path):
    path.write_text("control:\n  delay: 0.25\n", encoding="utf-8")

    loaded = cfg.load(path)

    assert loaded.control.delay == 0.25
    assert loaded.control.temp_window == cfg.Config().control.temp_window
    assert loaded.sensors.provider == cfg.Config().sensors.provider


def test_nested_sections_are_merged_not_replaced(path):
    path.write_text("sensors:\n  lhm_web:\n    timeout: 4.0\n", encoding="utf-8")

    loaded = cfg.load(path)

    assert loaded.sensors.lhm_web.timeout == 4.0
    assert loaded.sensors.lhm_web.url == cfg.Config().sensors.lhm_web.url


def test_unknown_keys_are_reported_and_ignored(path, caplog):
    path.write_text("control:\n  nonsense: 1\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = cfg.load(path)

    assert "control.nonsense" in caplog.text
    assert loaded == cfg.Config()


def test_a_non_mapping_document_is_rejected(path):
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match="mapping of sections"):
        cfg.load(path)


def test_broken_yaml_is_rejected_with_the_path(path):
    path.write_text("control:\n  delay: [unclosed\n", encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match="Cannot read"):
        cfg.load(path)


def test_a_digits_only_serial_stays_a_string(path):
    path.write_text("device:\n  serial: 12345678\n", encoding="utf-8")

    assert cfg.load(path).device.serial == "12345678"


# --- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("logging:\n  level: LOUD\n", "logging.level"),
        ("control:\n  delay: 0\n", "control.delay"),
        ("control:\n  temp_window: 0\n", "control.temp_window"),
        ("control:\n  max_step: -1\n", "control.max_step"),
        ("device:\n  baudrate: 0\n", "device.baudrate"),
        ("ui:\n  history_window: 0\n", "ui.history_window"),
        ("control:\n  curve: [[50, 40]]\n", "at least two"),
        ("control:\n  curve: [[50, 40], [40, 60]]\n", "strictly increase"),
        ("control:\n  curve: [[50, 40], [50, 60]]\n", "strictly increase"),
        ("control:\n  curve: [[50, 40], [60, 140]]\n", "outside 0-100"),
        ("control:\n  curve: [[50, 40], [60, 'hot']]\n", "must hold numbers"),
        # `match` is a regex, so the brackets in the message need escaping.
        ("control:\n  curve: [[50], [60, 40]]\n", r"must be \[temperature, percent\]"),
    ],
)
def test_invalid_values_are_rejected_by_key(path, document, message):
    path.write_text(document, encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match=message):
        cfg.load(path)


def test_log_level_is_normalised(path):
    path.write_text("logging:\n  level: debug\n", encoding="utf-8")

    assert cfg.load(path).logging.level == "DEBUG"


def test_a_decreasing_curve_is_allowed_but_reported(path, caplog):
    path.write_text("control:\n  curve: [[50, 80], [70, 40]]\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = cfg.load(path)

    assert loaded.control.curve == [[50, 80], [70, 40]]
    assert "percentages decrease" in caplog.text


# --- sparse saving ------------------------------------------------------------------------------


def test_defaults_write_an_empty_document(path):
    cfg.save(cfg.Config(), path)

    assert cfg.load(path) == cfg.Config()
    assert path.read_text(encoding="utf-8").strip() in ("", "{}")


def test_only_changed_values_are_written(path):
    config = cfg.Config()
    config.control.delay = 0.5
    config.sensors.provider = "lhm-web"

    cfg.save(config, path)
    text = path.read_text(encoding="utf-8")

    assert "delay: 0.5" in text
    assert "lhm-web" in text
    assert "temp_window" not in text, "unchanged keys must not be written"
    assert "baudrate" not in text
    assert "logging" not in text, "an untouched section must not appear at all"


def test_a_value_returning_to_its_default_is_removed(path):
    config = cfg.Config()
    config.control.max_step = 3
    cfg.save(config, path)
    assert "max_step" in path.read_text(encoding="utf-8")

    config.control.max_step = cfg.Config().control.max_step
    cfg.save(config, path)

    assert "max_step" not in path.read_text(encoding="utf-8")


def test_saving_round_trips(path):
    config = cfg.Config()
    config.device.timeout = 0.1
    config.control.curve = [[1, 48], [65, 48], [92, 100]]
    config.ui.history_window = 1800

    cfg.save(config, path)
    loaded = cfg.load(path)

    assert loaded.device.timeout == 0.1
    assert loaded.control.curve == [[1, 48], [65, 48], [92, 100]]
    assert loaded.ui.history_window == 1800


def test_curve_points_are_written_one_per_line(path):
    config = cfg.Config()
    config.control.curve = [[40, 10], [80, 90]]

    cfg.save(config, path)

    assert "- [40, 10]" in path.read_text(encoding="utf-8"), "points should read as pairs, not nested blocks"


def test_comments_survive_a_save(path):
    path.write_text(
        "control:\n  # tuned by hand on a hot day\n  delay: 0.5\n",
        encoding="utf-8",
    )
    config = cfg.load(path)
    config.control.max_step = 3

    cfg.save(config, path)
    text = path.read_text(encoding="utf-8")

    assert "tuned by hand on a hot day" in text
    assert "max_step: 3" in text


def test_save_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "config.yaml"
    config = cfg.Config()
    config.control.delay = 0.5

    cfg.save(config, target)

    assert target.exists()


# --- not writing when there is nothing to write --------------------------------------------------
#
# The window geometry is saved on every minimise and on exit, and most of those save exactly what is
# already on disk. Rewriting the file each time would spend flash write cycles to change nothing.


def test_saving_the_same_configuration_twice_writes_once(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    stamp = path.stat().st_mtime_ns

    cfg.save(config, path)

    assert path.stat().st_mtime_ns == stamp, "an unchanged configuration must not be rewritten"


def test_saving_an_actual_change_does_write(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    stamp = path.stat().st_mtime_ns

    config.control.delay = 0.75
    cfg.save(config, path)

    assert path.stat().st_mtime_ns != stamp
    assert "0.75" in path.read_text(encoding="utf-8")


def test_a_hand_edited_file_is_left_alone_when_it_already_matches(path):
    """Comments and formatting must not be reason enough to rewrite the file."""
    path.write_text("control:\n  # tuned by hand\n  delay: 0.5\n", encoding="utf-8")
    config = cfg.load(path)
    cfg.save(config, path)  # normalises once, if at all
    stamp = path.stat().st_mtime_ns

    cfg.save(config, path)

    assert path.stat().st_mtime_ns == stamp


def test_reverting_a_value_to_its_default_is_a_change(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    stamp = path.stat().st_mtime_ns

    config.control.delay = cfg.Config().control.delay
    cfg.save(config, path)

    assert path.stat().st_mtime_ns != stamp
    assert "delay" not in path.read_text(encoding="utf-8"), "back to default means dropped from the file"


def test_copy_is_detached(path):
    config = cfg.load(path)
    clone = cfg.copy(config)

    clone.control.curve.append([99, 100])
    clone.sensors.lhm_web.timeout = 9.0

    assert config.control.curve != clone.control.curve
    assert config.sensors.lhm_web.timeout != 9.0


# --- the .env era is over -----------------------------------------------------------------------


def test_no_dotenv_anywhere():
    """python-dotenv is gone; nothing may read a .env file."""
    from pathlib import Path

    package = Path(cfg.__file__).parent.parent
    offenders = []
    for source in list(package.rglob("*.py")) + list(package.rglob("*.pyw")):
        text = source.read_text(encoding="utf-8")
        if "dotenv" in text or "load_dotenv" in text:
            offenders.append(source.name)

    assert not offenders, offenders


def test_no_module_named_env_remains():
    from pathlib import Path

    assert not (Path(cfg.__file__).parent / "env.py").exists()


def test_integral_curve_values_are_written_without_a_decimal_point(path):
    config = cfg.Config()
    config.control.curve = [[40.0, 10.0], [80.0, 90.5]]

    cfg.save(config, path)
    text = path.read_text(encoding="utf-8")

    assert "- [40, 10]" in text
    assert "- [80, 90.5]" in text, "genuine fractions must survive"
