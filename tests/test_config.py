"""Configuration loading, validation and sparse saving."""

import os

import pytest

from iets_speed_control.util import config as cfg


@pytest.fixture
def path(tmp_path):
    return tmp_path / "config.yaml"


# --- isolation from the developer's own file ----------------------------------------------------


def test_a_save_without_a_path_lands_in_the_throwaway_folder():
    """The file the app runs on must survive a test that saves without naming a path."""
    from conftest import TEST_CONFIG_DIR

    real = cfg.DEFAULT_CONFIG_PATH
    before = real.read_bytes() if real.exists() else None
    config = cfg.Config()
    config.control.delay = 0.42

    written = cfg.save(config)

    assert written.parent == TEST_CONFIG_DIR
    assert "0.42" in written.read_text(encoding="utf-8")
    assert (real.read_bytes() if real.exists() else None) == before, "the developer's config.yaml changed"
    written.unlink()


def test_the_config_path_can_be_set_from_the_environment(tmp_path):
    """The suite relies on it, and it is how a second, experimental configuration is run."""
    import os
    import subprocess
    import sys

    target = tmp_path / "elsewhere.yaml"
    target.write_text("control:\n  delay: 0.33\n", encoding="utf-8")
    script = "from iets_speed_control.util.config import CONFIG, CONFIG_PATH; print(CONFIG_PATH, CONFIG.control.delay)"

    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "IETS_SPEED_CONTROL_CONFIG": str(target)},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    # The whole line, not split(): a profile folder with a space in its name is a normal path.
    assert result.stdout.strip() == f"{target} 0.33"


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
    assert loaded.sensors.tracked == {}


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
    config.sensors.tracked = {"lhm-web": ["Core Max"]}

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


# --- the tracked sensors --------------------------------------------------------------------------


def test_nothing_is_tracked_by_default(path):
    assert cfg.Config().sensors.tracked == {}
    assert cfg.load(path).sensors.tracked == {}


def test_a_tracked_map_is_read(path):
    path.write_text(
        "sensors:\n  tracked:\n    lhm-web:\n      - Core Max\n      - GPU Hot Spot\n    lenovo-wmi:\n      - PCH\n",
        encoding="utf-8",
    )

    assert cfg.load(path).sensors.tracked == {"lhm-web": ["Core Max", "GPU Hot Spot"], "lenovo-wmi": ["PCH"]}


def test_a_source_is_tracked_under_its_code_name_even_when_the_file_uses_an_alias(path):
    """Every reader looks a source up by its code name; under the alias its sensors would be lost."""
    path.write_text("sensors:\n  tracked:\n    ohm: [CPU Package]\n", encoding="utf-8")

    assert cfg.load(path).sensors.tracked == {"lhm": ["CPU Package"]}


def test_an_unknown_source_is_refused_with_the_known_ones(path):
    path.write_text("sensors:\n  tracked:\n    sensorama: [CPU]\n", encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match=r"sensors\.tracked\.sensorama: no such source.*aida64"):
        cfg.load(path)


def test_the_same_source_under_two_spellings_is_refused(path):
    path.write_text("sensors:\n  tracked:\n    ohm: [CPU]\n    lhm: [GPU]\n", encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match="already listed under another spelling"):
        cfg.load(path)


@pytest.mark.parametrize("document", ["sensors:\n  tracked:\n    aida64:\n", "sensors:\n  tracked:\n    aida64: ~\n"])
def test_a_source_with_nothing_under_it_tracks_no_sensor(path, document):
    """The picker adds a source before the user picks from it; the file has to read back the same way."""
    path.write_text(document, encoding="utf-8")

    assert cfg.load(path).sensors.tracked == {"aida64": []}


def test_an_empty_tracked_key_means_nothing_is_tracked(path):
    path.write_text("sensors:\n  tracked:\n", encoding="utf-8")

    assert cfg.load(path).sensors.tracked == {}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("sensors:\n  tracked: [CPU]\n", "must map a source"),
        ("sensors:\n  tracked:\n    aida64: CPU\n", r"sensors\.tracked\.aida64 must be a list"),
        ("sensors:\n  tracked:\n    aida64: ['']\n", r"aida64\[0\] must be a sensor name"),
        ("sensors:\n  tracked:\n    aida64: ['   ']\n", r"aida64\[0\] must be a sensor name"),
        ("sensors:\n  tracked:\n    aida64: [42]\n", r"aida64\[0\] must be a sensor name"),
        ("sensors:\n  tracked:\n    aida64: [CPU, ~]\n", r"aida64\[1\] must be a sensor name"),
        ("sensors:\n  tracked:\n    aida64: [CPU, GPU, CPU]\n", r"aida64\[2\].*already in the list"),
    ],
)
def test_unusable_tracked_sensors_are_refused_by_key(path, document, message):
    path.write_text(document, encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match=message):
        cfg.load(path)


def test_a_label_is_kept_as_written(path):
    """It is a sensor's exact name, not a pattern: a bracket is a bracket."""
    path.write_text("sensors:\n  tracked:\n    aida64: ['CPU (Tctl)']\n", encoding="utf-8")

    assert cfg.load(path).sensors.tracked == {"aida64": ["CPU (Tctl)"]}


def test_the_old_provider_and_filters_are_ignored_with_a_warning(path, caplog):
    """A breaking change: nothing is carried over, so the user picks the sensors again in Settings."""
    path.write_text("sensors:\n  provider: lenovo-wmi\n  filters:\n    lenovo-wmi: [CPU, GPU, PCH]\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = cfg.load(path)

    assert loaded.sensors.tracked == {}, "an old setting must not start tracking anything"
    assert "Ignoring unknown configuration key sensors.provider" in caplog.text
    assert "Ignoring unknown configuration key sensors.filters" in caplog.text


@pytest.mark.parametrize("key", ["cpu_filter", "gpu_filter"])
def test_the_older_filter_keys_are_ignored_too(path, caplog, key):
    path.write_text(f"sensors:\n  {key}: Core Max\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = cfg.load(path)

    assert loaded.sensors.tracked == {}
    assert f"Ignoring unknown configuration key sensors.{key}" in caplog.text


def test_saving_drops_the_old_keys(path):
    path.write_text(
        "sensors:\n  provider: lenovo-wmi\n  filters:\n    lenovo-wmi: [CPU, GPU, PCH]\n"
        "  tracked:\n    lhm-web: [Core Max]\n",
        encoding="utf-8",
    )

    cfg.save(cfg.load(path), path)
    text = path.read_text(encoding="utf-8")

    assert "provider" not in text
    assert "filters" not in text
    assert cfg.load(path).sensors.tracked == {"lhm-web": ["Core Max"]}


def test_a_tracked_set_survives_a_save_and_a_load(path):
    config = cfg.Config()
    config.sensors.tracked = {"lenovo-wmi": ["CPU", "GPU"], "nvidia": []}

    cfg.save(config, path)

    assert cfg.load(path).sensors.tracked == {"lenovo-wmi": ["CPU", "GPU"], "nvidia": []}


def test_a_copy_does_not_share_the_lists():
    config = cfg.Config()
    config.sensors.tracked["aida64"] = ["CPU"]

    copied = cfg.copy(config)
    copied.sensors.tracked["aida64"].append("GPU")

    assert config.sensors.tracked["aida64"] == ["CPU"]


# --- the default speed ----------------------------------------------------------------------------


def test_the_default_speed_is_half_and_is_not_written_while_it_is_half(path):
    assert cfg.Config().control.default_speed == 50
    cfg.save(cfg.Config(), path)

    assert "default_speed" not in path.read_text(encoding="utf-8")


def test_a_changed_default_speed_is_written_and_read_back(path):
    config = cfg.Config()
    config.control.default_speed = 35

    cfg.save(config, path)

    assert "default_speed: 35" in path.read_text(encoding="utf-8")
    assert cfg.load(path).control.default_speed == 35


@pytest.mark.parametrize("speed", [0, 1, 100])
def test_a_default_speed_inside_the_range_is_accepted(path, speed):
    path.write_text(f"control:\n  default_speed: {speed}\n", encoding="utf-8")

    assert cfg.load(path).control.default_speed == speed


@pytest.mark.parametrize("speed", ["-1", "101", "50.5", "true", "'50'", "~"])
def test_a_default_speed_that_is_not_a_whole_number_within_0_100_is_refused(path, speed):
    path.write_text(f"control:\n  default_speed: {speed}\n", encoding="utf-8")

    with pytest.raises(cfg.ConfigError, match=r"control\.default_speed must be a whole number within 0-100"):
        cfg.load(path)


def test_every_alias_names_a_registered_source():
    from iets_speed_control.sensors import PROVIDERS
    from iets_speed_control.util.source_names import ALIASES

    assert set(ALIASES.values()) <= set(PROVIDERS)


def test_the_source_names_are_exactly_the_registered_sources():
    """The configuration cannot import the registry without a cycle, so it keeps its own list of names."""
    from iets_speed_control.sensors import PROVIDERS
    from iets_speed_control.util.source_names import SOURCE_NAMES

    assert set(SOURCE_NAMES) == set(PROVIDERS)
    assert len(SOURCE_NAMES) == len(set(SOURCE_NAMES))


def test_a_relative_config_path_is_made_absolute_at_once(tmp_path):
    """Otherwise the file in use would depend on the folder the app happened to start in."""
    import os
    import subprocess
    import sys

    script = "from iets_speed_control.util.config import CONFIG_PATH; print(CONFIG_PATH)"

    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "IETS_SPEED_CONTROL_CONFIG": "relative.yaml"},
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    assert result.stdout.strip() == str(tmp_path / "relative.yaml")


@pytest.mark.parametrize("document", ["control: 5\n", "device:\n  timeout: abc\n", "logging:\n  level: 5\n"])
def test_a_developer_config_of_the_wrong_shape_does_not_stop_the_e2e_tests(tmp_path, monkeypatch, document):
    """load() raises more than ConfigError for such a file; the e2e set-up must shrug it off."""
    from conftest import use_this_machines_web_server

    own = tmp_path / "config.yaml"
    own.write_text(document, encoding="utf-8")
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", own)
    before = cfg.CONFIG.sensors.lhm_web

    use_this_machines_web_server()

    assert cfg.CONFIG.sensors.lhm_web is before


def test_an_unusable_web_server_url_reads_as_unavailable(monkeypatch):
    """An empty URL in the developer's file must skip the lhm-web e2e tests, not error them."""
    from conftest import probe_lhm_web

    monkeypatch.setattr(cfg.CONFIG.sensors.lhm_web, "url", "")

    assert probe_lhm_web() == {}
