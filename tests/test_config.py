"""Configuration loading, validation and sparse saving."""

import os

import pytest

from iets_speed_control.util import config as cfg


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config.yaml"


# --- isolation from the developer's own file ----------------------------------------------------


def test_the_suite_writes_to_a_throwaway_folder_not_the_real_config():
    """A save() without a path must not overwrite the file the app runs on."""
    from pathlib import Path

    real = Path.home() / ".iets-speed-control"

    assert real not in cfg.CONFIG_PATH.parents
    assert real != cfg.CONFIG_DIR


def test_the_suite_starts_from_the_defaults():
    """The developer's own config.yaml is loaded on import; the suite must not see any of it."""
    assert cfg.to_sparse(cfg.CONFIG) == {}


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
        ("sensors:\n  provider: null\n", "sensors.provider"),
        ("sensors:\n  provider: 42\n", "sensors.provider"),
        ("sensors:\n  provider: ' '\n", "sensors.provider"),
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
#
# Each test backdates the file before the second save. Two writes in a row can land on the same
# timestamp on a fast disk, so comparing the timestamps of two saves cannot tell a rewrite apart.


OLD_NS = 1_000_000_000_000_000_000  # 2001-09-09: any rewrite now moves the timestamp away from it


def backdate(path):
    os.utime(path, ns=(OLD_NS, OLD_NS))


def test_saving_the_same_configuration_twice_writes_once(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    backdate(path)

    cfg.save(config, path)

    assert path.stat().st_mtime_ns == OLD_NS, "an unchanged configuration must not be rewritten"


def test_saving_an_actual_change_does_write(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    backdate(path)

    config.control.delay = 0.75
    cfg.save(config, path)

    assert path.stat().st_mtime_ns != OLD_NS
    assert "0.75" in path.read_text(encoding="utf-8")


def test_a_hand_edited_file_is_left_alone_when_it_already_matches(path):
    """Comments and formatting must not be reason enough to rewrite the file."""
    path.write_text("control:\n  # tuned by hand\n  delay: 0.5\n", encoding="utf-8")
    config = cfg.load(path)
    cfg.save(config, path)  # normalises once, if at all
    backdate(path)

    cfg.save(config, path)

    assert path.stat().st_mtime_ns == OLD_NS


def test_reverting_a_value_to_its_default_is_a_change(path):
    config = cfg.Config()
    config.control.delay = 0.5
    cfg.save(config, path)
    backdate(path)

    config.control.delay = cfg.Config().control.delay
    cfg.save(config, path)

    assert path.stat().st_mtime_ns != OLD_NS
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


# --- the filter list ------------------------------------------------------------------------------


def test_a_source_without_its_own_filters_uses_the_defaults():
    assert cfg.filters_for(cfg.Config(), "aida64") == ["CPU", "GPU"]


def test_a_source_keeps_its_own_filters(path):
    path.write_text("sensors:\n  filters:\n    lhm-web:\n      - Core Max\n      - GPU Hot Spot\n", encoding="utf-8")

    config = cfg.load(path)

    assert cfg.filters_for(config, "lhm-web") == ["Core Max", "GPU Hot Spot"]
    assert cfg.filters_for(config, "aida64") == ["CPU", "GPU"], "other sources keep the defaults"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("sensors:\n  filters: [CPU]\n", "must map a source"),
        ("sensors:\n  filters:\n    aida64: []\n", "at least one filter"),
        ("sensors:\n  filters:\n    aida64: CPU\n", "at least one filter"),
        ("sensors:\n  filters:\n    aida64: ['CPU (']\n", r"aida64\[0\].*not a valid regular expression"),
        ("sensors:\n  filters:\n    aida64: ['']\n", "non-empty text"),
    ],
)
def test_unusable_filters_are_refused_by_key(path, document, message):
    path.write_text(document, encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match=message):
        cfg.load(path)


def test_the_old_cpu_and_gpu_filters_become_the_sources_list(path):
    path.write_text(
        "sensors:\n  provider: lhm-web\n  cpu_filter: CPU Package\n  gpu_filter: GPU Hot Spot\n", encoding="utf-8"
    )

    config = cfg.load(path)

    assert cfg.filters_for(config, "lhm-web") == [r"CPU\ Package", r"GPU\ Hot\ Spot"]


def test_an_old_filter_keeps_matching_its_text_literally(path):
    """They were plain text, so "(Tctl)" must not turn into a regex group."""
    from iets_speed_control.util.filters import select

    path.write_text("sensors:\n  cpu_filter: CPU (Tctl)\n", encoding="utf-8")

    patterns = cfg.filters_for(cfg.load(path), "aida64")

    assert select({"CPU (Tctl)": 70.0, "CPU Tctl": 99.0}, patterns).max_value == 70.0


def test_a_missing_old_key_keeps_its_old_default(path):
    """A file with only cpu_filter still meant GPU for the GPU."""
    path.write_text("sensors:\n  cpu_filter: Core Max\n", encoding="utf-8")

    assert cfg.filters_for(cfg.load(path), "aida64") == [r"Core\ Max", "GPU"]


def test_old_filters_equal_to_the_defaults_leave_no_entry(path):
    path.write_text("sensors:\n  cpu_filter: CPU\n  gpu_filter: GPU\n", encoding="utf-8")

    assert cfg.load(path).sensors.filters == {}


def test_old_filters_do_not_override_a_list_already_there(path, caplog):
    path.write_text("sensors:\n  cpu_filter: Core Max\n  filters:\n    aida64: [Package]\n", encoding="utf-8")

    config = cfg.load(path)

    assert cfg.filters_for(config, "aida64") == ["Package"]
    assert any("Ignoring" in record.message for record in caplog.records)


def test_old_filter_keys_are_not_reported_as_unknown(path, caplog):
    path.write_text("sensors:\n  cpu_filter: Core Max\n", encoding="utf-8")

    cfg.load(path)

    assert not any("unknown" in record.message for record in caplog.records)


def test_saving_writes_the_list_and_drops_the_old_keys(path):
    path.write_text("sensors:\n  cpu_filter: Core Max\n", encoding="utf-8")

    cfg.save(cfg.load(path), path)
    text = path.read_text(encoding="utf-8")

    assert "cpu_filter" not in text
    assert cfg.filters_for(cfg.load(path), "aida64") == [r"Core\ Max", "GPU"]


def test_a_copy_does_not_share_the_lists():
    config = cfg.Config()
    config.sensors.filters["aida64"] = ["CPU"]

    copied = cfg.copy(config)
    copied.sensors.filters["aida64"].append("GPU")

    assert config.sensors.filters["aida64"] == ["CPU"]
