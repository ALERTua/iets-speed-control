"""The settings panel: every configuration key it now edits, and what each edit applies to.

Two properties matter more than the widgets themselves:
  * a value the app could not start with must never reach the live configuration, and
  * a key whose change needs code (the sensor source, the smoothing window, the serial port) must
    actually reach that code, not just the config object.

Tk delivers synthesized events only to mapped widgets, and only one section is packed at a time, so
every test that types into a row shows that section first. Skipping this turns the "bad input is
refused" tests into assertions that nothing happened, which is true when nothing happened at all.
"""

from dataclasses import fields

import pytest
from conftest import FakeProvider

from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG

ctk = pytest.importorskip("customtkinter")


def test_the_default_log_path_button_fills_the_field(view, isolated_logging):
    """No file is written unless a path is set, so turning one on has to be one click."""
    from iets_speed_control.util.logger import default_log_file

    CONFIG.logging.file = None
    row_named(view, "Logging", "Log file").entry.delete(0, "end")

    row_named(view, "Logging", "Use the default path").control.cget("command")()

    assert CONFIG.logging.file == str(default_log_file())
    assert row_named(view, "Logging", "Log file").entry.get() == str(default_log_file())


def test_an_empty_log_file_means_no_file(view):
    CONFIG.logging.file = "somewhere.log"

    edit(view, "Logging", "Log file", "  ")

    assert CONFIG.logging.file is None, "empty is how the file is turned off"


@pytest.fixture
def isolated_logging():
    """Applying a level rebuilds the global handlers, so put them back for the tests that follow."""
    import logging

    from iets_speed_control.util import logger as logger_module

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


def row_named(view, section, title):
    for row in view.sections[section].rows:
        if row.title == title:
            return row

    raise AssertionError(f"no row titled {title!r} in {section}; have {[r.title for r in view.sections[section].rows]}")


def type_into(widget, text):
    """Replace the contents and press Enter.

    Two details decide whether the binding runs at all. CTkEntry.bind attaches to the tkinter.Entry
    inside the frame, so the event has to be aimed at that inner widget; and Tk routes key events to
    whatever holds the focus, so the inner widget has to be given it first.
    """
    widget.delete(0, "end")
    widget.insert(0, text)
    # focus_set only requests focus; without forcing it and letting Tk process the request, whether
    # the key event arrives depends on what the previous test left focused.
    widget._entry.focus_force()
    widget.update()
    widget._entry.event_generate("<Return>")


def edit(view, section, title, text):
    """Show a section, type into one of its rows, and hand the row back."""
    view.show(section)
    view.update()
    row = row_named(view, section, title)
    type_into(row.entry, text)
    return row


def choose(row, label):
    """Pick a value from an option menu. set() does not fire the command, so call it as a click does."""
    row.menu.set(label)
    row.menu.cget("command")(label)


# --- coverage of the schema ---------------------------------------------------------------------


def every_path(section, prefix="") -> list[str]:
    paths = []
    for f in fields(section):
        value = getattr(section, f.name)
        if hasattr(value, "__dataclass_fields__"):
            paths.extend(every_path(value, f"{prefix}{f.name}."))
        else:
            paths.append(f"{prefix}{f.name}")
    return paths


def test_every_configuration_key_is_editable(view):
    """The point of the exercise: no key left that only a text editor can reach."""
    editable = {path for section in view.sections.values() for path, _refresh in section.bindings}
    # Handled by their own widgets rather than by a generic row.
    editable |= {"control.curve", "ui.history_window", "ui.rail_collapsed"}
    # Remembered from what the user did to the window, not typed anywhere.
    editable |= {"ui.window_x", "ui.window_y", "ui.window_width", "ui.window_height"}

    missing = [path for path in every_path(CONFIG) if path not in editable]
    assert not missing, f"not reachable from the GUI: {missing}"


def test_rows_show_what_the_configuration_holds(view):
    assert row_named(view, "Device", "Serial port").combo.get() == CONFIG.device.port
    assert row_named(view, "Device", "Baud rate").menu.get() == str(CONFIG.device.baudrate)
    assert row_named(view, "Control", "Response time").entry.get() == str(CONFIG.control.delay)
    assert row_named(view, "Logging", "Log level").menu.get() == CONFIG.logging.level


# --- writing -------------------------------------------------------------------------------------


def test_an_entry_writes_its_key(view):
    edit(view, "Control", "Hysteresis", "7")

    assert CONFIG.control.ignore_less_than == 7


def test_a_switch_writes_its_key(view):
    row = row_named(view, "Display", "Start minimized")
    assert CONFIG.ui.minimize_on_launch is True, "test setup: the default is on"

    row.switch.deselect()
    row.switch.cget("command")()

    assert CONFIG.ui.minimize_on_launch is False
    assert isinstance(CONFIG.ui.minimize_on_launch, bool), "the file should hold true/false, not 0/1"


def test_a_switch_shows_what_the_configuration_holds(view):
    CONFIG.ui.hide_to_tray_on_minimize = False
    view.sections["Display"].bindings[-1][1]()

    assert not row_named(view, "Display", "Minimize to tray").switch.get()


def test_a_choice_writes_its_key(view):
    choose(row_named(view, "Device", "Baud rate"), "9600")

    assert CONFIG.device.baudrate == 9600
    assert isinstance(CONFIG.device.baudrate, int), "the menu shows text but the device needs a number"


def test_an_empty_optional_becomes_none(view):
    edit(view, "Device", "Device serial number", "   ")

    assert CONFIG.device.serial is None


def test_a_digits_only_serial_stays_a_string(view):
    """Matching does a substring test, so an int serial would blow up rather than not match."""
    edit(view, "Device", "Device serial number", "568022419")

    assert CONFIG.device.serial == "568022419"


def test_the_log_level_is_normalised(view):
    """The menu only offers upper case; a hand-edited file may not. What is stored is validated."""
    view._commit("logging.level", "debug")

    assert CONFIG.logging.level == "DEBUG"


def test_the_log_level_reaches_the_logger(view, isolated_logging):
    import logging

    from iets_speed_control.util.logger import PACKAGE_LOGGER

    choose(row_named(view, "Logging", "Log level"), "WARNING")

    assert logging.getLogger(PACKAGE_LOGGER).level == logging.WARNING


# --- refusing bad values -------------------------------------------------------------------------


def test_a_non_numeric_value_is_refused_and_reverted(view):
    before = CONFIG.control.delay

    row = edit(view, "Control", "Response time", "soon")

    assert row.error is not None and row.error.winfo_manager(), "the edit never reached the handler"
    assert CONFIG.control.delay == before, "a value that does not parse must not reach the configuration"
    assert row.entry.get() == str(before), "the entry must show what is actually stored"


def test_a_value_validate_rejects_is_refused(view):
    before = CONFIG.control.delay

    row = edit(view, "Control", "Response time", "0")

    assert "greater than zero" in row.error.cget("text")
    assert CONFIG.control.delay == before


def test_a_negative_step_is_refused(view):
    before = CONFIG.control.max_step

    row = edit(view, "Control", "Max ramp-down step", "-5")

    assert "must not be negative" in row.error.cget("text")
    assert CONFIG.control.max_step == before


def test_an_empty_required_value_is_refused(view):
    before = CONFIG.device.pwm_command

    row = edit(view, "Device", "PWM command", "")

    assert "cannot be empty" in row.error.cget("text")
    assert CONFIG.device.pwm_command == before


def test_a_rejected_edit_clears_once_a_good_one_lands(view):
    row = edit(view, "Control", "Response time", "0")
    assert row.error.winfo_manager()

    type_into(row.entry, "2")

    assert CONFIG.control.delay == 2.0
    assert not row.error.winfo_manager()


@pytest.fixture
def web_server_rows(view):
    """Show the web-server rows, which exist only for lhm-web while the default source is aida64.

    A hidden row has no width and takes no typing.
    """
    CONFIG.sensors.provider = "lhm-web"
    view._apply_lhm_visibility()


def test_a_bad_web_server_timeout_is_refused(view, web_server_rows):
    before = CONFIG.sensors.lhm_web.timeout

    row = edit(view, "Sensors", "Web server timeout", "0")

    assert "greater than zero" in row.error.cget("text")
    assert CONFIG.sensors.lhm_web.timeout == before


# --- what an edit applies to ---------------------------------------------------------------------


def test_the_source_menu_shows_product_names(view):
    """The file keeps short code names; a menu reading "lhm-web" tells a user nothing."""
    from iets_speed_control.sensors import PROVIDER_LABELS

    menu = row_named(view, "Sensors", "Temperature source").menu

    assert "LibreHardwareMonitor (web server)" in menu.cget("values")
    assert "lhm-web" not in menu.cget("values")
    assert menu.get() == PROVIDER_LABELS[CONFIG.sensors.provider]


def test_the_source_menu_still_writes_the_code_name(view):
    choose(row_named(view, "Sensors", "Temperature source"), "AIDA64")

    assert CONFIG.sensors.provider == "aida64", "the label must not reach the configuration file"


def test_changing_the_source_swaps_the_provider(view):
    from iets_speed_control.sensors import LibreHardwareMonitorWebProvider

    choose(row_named(view, "Sensors", "Temperature source"), "LibreHardwareMonitor (web server)")

    assert CONFIG.sensors.provider == "lhm-web"
    assert isinstance(view.controller.sensors, LibreHardwareMonitorWebProvider), (
        "writing the key is not enough: the running controller must read from the new source"
    )


def test_changing_the_smoothing_window_rebuilds_the_smoothers(view):
    before = view.controller._smoother

    edit(view, "Control", "Smoothing window", "9")

    assert CONFIG.control.temp_window == 9
    assert view.controller.temp_window == 9
    assert view.controller._smoother is not before, "a resized window needs a new smoother"
    assert view.controller._smoother.window == 9


@pytest.fixture
def unelevated(monkeypatch):
    from iets_speed_control.sensors import base

    monkeypatch.setattr(base, "is_elevated", lambda: False)


def test_picking_the_lenovo_source_without_admin_rights_says_why_it_will_not_read(view, unelevated):
    from iets_speed_control.gui.settings import ADMIN_NOTE
    from iets_speed_control.gui.theme import ERROR_COLOR

    choose(row_named(view, "Sensors", "Temperature source"), "Lenovo Legion (WMI)")

    assert CONFIG.sensors.provider == "lenovo-wmi"
    assert view.admin_label.winfo_manager(), "the pick clears the row's error line; the note must survive it"
    assert view.admin_label.cget("text") == ADMIN_NOTE
    assert view.admin_label.cget("text_color") == ERROR_COLOR


def test_the_admin_note_goes_away_with_the_lenovo_source(view, unelevated):
    choose(row_named(view, "Sensors", "Temperature source"), "Lenovo Legion (WMI)")

    choose(row_named(view, "Sensors", "Temperature source"), "AIDA64")

    assert not view.admin_label.winfo_manager()


def test_an_elevated_app_shows_no_admin_note(view, monkeypatch):
    from iets_speed_control.sensors import LenovoWmiProvider, base

    monkeypatch.setattr(base, "is_elevated", lambda: True)
    # The pick reads the new source once in the background; keep that off the real hardware.
    monkeypatch.setattr(LenovoWmiProvider, "get_temperatures", lambda self: {})

    choose(row_named(view, "Sensors", "Temperature source"), "Lenovo Legion (WMI)")

    assert not view.admin_label.winfo_manager()


def test_a_hand_written_alias_still_gets_the_admin_note(view, unelevated):
    CONFIG.sensors.provider = "lenovo"

    view._apply_admin_note()

    assert view.admin_label.winfo_manager()


def test_asking_a_row_for_a_combo_it_does_not_have_is_an_error(view):
    with pytest.raises(TypeError, match="Temperature source"):
        row_named(view, "Sensors", "Temperature source").require_combo()


def test_a_combo_row_hands_back_its_combo(view):
    row = row_named(view, "Device", "Serial port")

    assert row.require_combo() is row.combo


def test_the_web_server_rows_appear_only_for_that_source(view):
    CONFIG.sensors.provider = "aida64"
    view._apply_lhm_visibility()
    assert not row_named(view, "Sensors", "Web server URL").text.winfo_manager()

    CONFIG.sensors.provider = "lhm-web"
    view._apply_lhm_visibility()

    assert row_named(view, "Sensors", "Web server URL").text.winfo_manager()


def test_the_password_is_masked(view):
    assert row_named(view, "Sensors", "Web server password").entry.cget("show") == "*"


def test_the_web_server_url_reaches_the_provider_without_a_rebuild(view, web_server_rows):
    """The provider reads the configuration per request, so editing the URL takes effect at once."""
    from iets_speed_control.sensors import LibreHardwareMonitorWebProvider

    provider = LibreHardwareMonitorWebProvider()
    edit(view, "Sensors", "Web server URL", "http://elsewhere:9000/data.json")

    assert provider.url == "http://elsewhere:9000/data.json"


# --- row layout ----------------------------------------------------------------------------------


def test_no_description_is_cut_off(view, tk_root, web_server_rows, monkeypatch):
    """Descriptions have to fit the column they are in, because nothing tells you when they do not.

    Grid sizes the control column to the widest control in the whole card, so one wide control
    narrows every description in that card, and the surplus is silently clipped mid-word. Wrapping is
    not a way out: a CTkLabel keeps its one-line height and simply does not draw the second line.

    Measured at the narrowest window the app allows, since that is where the columns are tightest.
    """
    from iets_speed_control.gui import settings
    from iets_speed_control.gui.theme import RAIL_EXPANDED_WIDTH, WINDOW_MIN_SIZE

    # The web-server rows show only for lhm-web, and the admin note only for a source that needs
    # rights this process lacks. Neither real setup shows both, so the note is shown on its own terms.
    monkeypatch.setattr(settings, "lacks_admin_rights", lambda provider: True)
    view._apply_admin_note()
    assert view.admin_label.winfo_manager(), "test setup: the note must be on screen to be measured"

    was = tk_root.geometry()
    content_width = WINDOW_MIN_SIZE[0] - RAIL_EXPANDED_WIDTH
    tk_root.geometry(f"{content_width}x{WINDOW_MIN_SIZE[1]}")
    try:
        too_wide = []
        for name in view.SECTION_NAMES:
            view.show(name)
            view.update()
            for row in view.sections[name].rows:
                if row.subtitle_label is None:
                    continue

                room = row.text.winfo_width()
                needed = row.subtitle_label.winfo_reqwidth()
                if needed > room:
                    too_wide.append(f"{name}/{row.title}: needs {needed}px, has {room}px: {row.subtitle!r}")

            # The admin note sits under the source selector's description, in the same column.
            if name == "Sensors":
                room = view.provider_row.text.winfo_width()
                needed = view.admin_label.winfo_reqwidth()
                if needed > room or view.admin_label.winfo_width() < needed:
                    too_wide.append(f"Sensors/admin note: needs {needed}px, has {room}px")
    finally:
        tk_root.geometry(was)
        tk_root.update()

    assert not too_wide, f"at {content_width}px these descriptions are cut off:\n" + "\n".join(too_wide)


# --- the filter list ------------------------------------------------------------------------------


def line_with(view, pattern):
    for line in view.filter_list.lines:
        if line.pattern == pattern:
            return line

    raise AssertionError(f"no filter {pattern!r}; have {view.filter_list.patterns}")


def test_the_list_starts_with_the_sources_filters(view):
    assert view.filter_list.patterns == ["CPU", "GPU"]


def test_each_filter_shows_what_it_catches_and_the_maximum_is_marked(view):
    from iets_speed_control.gui.theme import MAX_COLOR, MUTED

    view._show_sensor_match(True, FakeProvider().get_temperatures())

    cpu, gpu = line_with(view, "CPU").result, line_with(view, "GPU").result
    assert "61" in cpu.cget("text") and "Fake CPU/Core Max" in cpu.cget("text")
    assert "max" in cpu.cget("text"), "the filter giving the maximum is the one the curve follows"
    assert cpu.cget("text_color") == MAX_COLOR
    assert "44" in gpu.cget("text") and "max" not in gpu.cget("text")
    assert gpu.cget("text_color") == MUTED


def test_a_filter_matching_nothing_is_called_out(view):
    from iets_speed_control.gui.filter_list import NO_MATCH
    from iets_speed_control.gui.theme import ERROR_COLOR

    CONFIG.sensors.filters["fake"] = ["Nonexistent", "CPU"]
    view._redisplay_filters()

    view._show_sensor_match(True, FakeProvider().get_temperatures())

    result = line_with(view, "Nonexistent").result
    assert result.cget("text") == NO_MATCH
    assert result.cget("text_color") == ERROR_COLOR, "0 °C holds the fan at its minimum; say so"


def test_the_sources_sensors_are_offered_as_filters(view):
    view._show_sensor_match(True, FakeProvider().get_temperatures())

    assert "Fake GPU/Hot Spot" in line_with(view, "CPU").combo.cget("values")


def test_a_picked_sensor_is_stored_literally(view):
    import re

    line_with(view, "CPU")._picked("Fake CPU/Core Max")

    assert CONFIG.sensors.filters["fake"] == [re.escape("Fake CPU/Core Max"), "GPU"]


def test_a_broken_expression_is_refused_with_the_reason(view):
    line = line_with(view, "CPU")
    line.combo.set("CPU (")

    view.filter_list.commit()

    assert "not a valid regular expression" in view.filters_row.error.cget("text")
    assert CONFIG.sensors.filters == {}, "a filter the app could not use must not reach the configuration"


def test_removing_a_filter_stores_the_rest(view):
    view.filter_list._remove(line_with(view, "GPU"))

    assert CONFIG.sensors.filters["fake"] == ["CPU"]
    assert view.filter_list.patterns == ["CPU"]


def test_the_last_filter_cannot_be_removed(view):
    CONFIG.sensors.filters["fake"] = ["CPU"]
    view._redisplay_filters()

    view.filter_list._remove(line_with(view, "CPU"))

    assert view.filter_list.patterns == ["CPU"]
    assert CONFIG.sensors.filters["fake"] == ["CPU"]
    assert "at least one" in view.filters_row.error.cget("text")


def test_a_new_line_counts_once_it_has_text(view):
    view.filter_list.add_filter()
    assert view.filter_list.patterns == ["CPU", "GPU"], "an empty line is not a filter yet"

    view.filter_list.lines[-1].combo.set("Hot Spot")
    view.filter_list.commit()

    assert CONFIG.sensors.filters["fake"] == ["CPU", "GPU", "Hot Spot"]


def test_going_back_to_the_defaults_writes_nothing(view):
    view.filter_list._remove(line_with(view, "GPU"))
    view.filter_list.add_filter()
    view.filter_list.lines[-1].combo.set("GPU")

    view.filter_list.commit()

    assert "fake" not in CONFIG.sensors.filters, "a list equal to the defaults is not written to the file"


def test_the_list_follows_the_source(view, monkeypatch):
    from iets_speed_control.sensors import LibreHardwareMonitorWebProvider

    monkeypatch.setattr(LibreHardwareMonitorWebProvider, "get_temperatures", lambda self: {})
    CONFIG.sensors.filters["lhm-web"] = ["Core Max"]

    choose(row_named(view, "Sensors", "Temperature source"), "LibreHardwareMonitor (web server)")

    assert view.filter_list.patterns == ["Core Max"]


def test_reset_puts_the_default_filters_back(view, monkeypatch):
    from iets_speed_control.sensors import Aida64Provider

    # Reset also puts the source back to aida64, whose read would otherwise reach the real WMI.
    monkeypatch.setattr(Aida64Provider, "get_temperatures", lambda self: {})
    CONFIG.sensors.filters["fake"] = ["Core Max"]
    view._redisplay_filters()

    view._reset_section(view.sections["Sensors"])

    assert CONFIG.sensors.filters == {}
    assert view.filter_list.patterns == ["CPU", "GPU"]


def test_a_failed_read_is_reported_on_the_filters_row(view):
    view._show_sensor_match(False, OSError("connection refused"))

    assert "connection refused" in view.filters_row.error.cget("text")


def test_a_source_with_no_readings_is_reported(view):
    """The loop then drives the fan from 0 °C, so this is the state worth saying out loud."""
    view._show_sensor_match(True, {})

    assert "No readings" in view.filters_row.error.cget("text")


def test_a_filter_typed_and_entered_takes_effect(view):
    """Enter in the combo box is how a typed filter reaches the configuration."""
    view.show("Sensors")
    view.update()
    view.filter_list.add_filter()
    combo = view.filter_list.lines[-1].combo

    combo.set("Hot Spot")
    combo._entry.focus_force()
    combo.update()
    combo._entry.event_generate("<Return>")
    view.update()

    assert CONFIG.sensors.filters["fake"] == ["CPU", "GPU", "Hot Spot"]


def test_an_empty_new_line_is_not_shown_as_a_filter_that_matches_nothing(view):
    from iets_speed_control.gui.filter_list import NO_MATCH

    view.filter_list.add_filter()

    view._show_sensor_match(True, FakeProvider().get_temperatures())

    assert view.filter_list.lines[-1].result.cget("text") != NO_MATCH


def test_the_maximum_is_marked_on_the_right_line_when_patterns_repeat_in_the_selection(view):
    """Lines pair with the matches in order, not by text, so equal texts cannot hide the marker."""
    from iets_speed_control.util.filters import select

    view.filter_list.set_patterns(["CPU", "CPU"])

    view.filter_list.show_selection(select(FakeProvider().get_temperatures(), ["CPU", "CPU"]))

    marked = ["max" in line.result.cget("text") for line in view.filter_list.lines]
    assert marked == [True, False]


def test_the_filter_list_does_not_widen_the_control_column(view):
    """The list spans the card; its combo boxes beside the descriptions would squeeze them all."""
    assert view.filter_list.grid_info()["columnspan"] == 2
    assert view.filters_row.control.winfo_reqwidth() <= 120


def test_background_work_lands_on_the_tk_thread(view, tk_root):
    """Sensor reads block, so they run off the Tk thread and come back through a queue."""
    seen = []
    view._in_background(lambda: 42, lambda ok, payload: seen.append((ok, payload)))

    for _ in range(60):
        tk_root.update()
        if seen:
            break
        tk_root.after(20, tk_root.quit)
        tk_root.mainloop()

    assert seen == [(True, 42)]


def test_background_failures_are_reported_not_raised(view, tk_root):
    seen = []
    view._in_background(lambda: 1 / 0, lambda ok, payload: seen.append((ok, type(payload))))

    for _ in range(60):
        tk_root.update()
        if seen:
            break
        tk_root.after(20, tk_root.quit)
        tk_root.mainloop()

    assert seen == [(False, ZeroDivisionError)]


# --- reset ---------------------------------------------------------------------------------------


def test_reset_puts_a_section_back_to_defaults(view):
    CONFIG.control.delay = 4.0
    CONFIG.control.max_step = 3

    view._reset_section(view.sections["Control"])

    reference = cfg.defaults()
    assert CONFIG.control.delay == reference.control.delay
    assert CONFIG.control.max_step == reference.control.max_step
    assert row_named(view, "Control", "Response time").entry.get() == str(reference.control.delay)


def test_reset_leaves_other_sections_alone(view):
    CONFIG.device.port = "COM42"

    view._reset_section(view.sections["Control"])

    assert CONFIG.device.port == "COM42"


def test_reset_reapplies_the_smoothing_window(view):
    edit(view, "Control", "Smoothing window", "11")
    assert view.controller.temp_window == 11

    view._reset_section(view.sections["Control"])

    assert view.controller.temp_window == cfg.defaults().control.temp_window


# --- search --------------------------------------------------------------------------------------


def test_search_finds_the_new_rows(view):
    view.search.insert(0, "baud")
    view.filter()

    assert view.sections["Device"].holder.winfo_manager()
    assert not view.sections["Curve"].holder.winfo_manager()


def test_search_matches_descriptions_too(view):
    view.search.insert(0, "rolling median")
    view.filter()

    assert view.sections["Control"].holder.winfo_manager()


def test_the_dead_sensor_filter_note_is_gone(view):
    titles = [row.title for row in view.sections["Manual"].rows]

    assert titles == ["Control mode", "Manual speed"]


def test_a_good_read_clears_the_error_of_an_earlier_one(view):
    """Switching from a silent source to a working one must not leave the old warning up."""
    view._show_sensor_match(True, {})
    assert view.filters_row.error.winfo_manager(), "test setup: the empty read must have warned"

    view._show_sensor_match(True, FakeProvider().get_temperatures())

    assert not view.filters_row.error.winfo_manager()


def test_a_failed_read_clears_the_results_of_an_earlier_one(view):
    view._show_sensor_match(True, FakeProvider().get_temperatures())

    view._show_sensor_match(False, OSError("gone"))

    assert all(line.result.cget("text") == "" for line in view.filter_list.lines)


def test_a_read_that_finishes_after_a_newer_one_started_is_dropped(view, monkeypatch):
    """A slow read of the old source must not paint its verdict over the new source."""
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)
    view.refresh_sensor_match()
    stale = view._match_request
    view.refresh_sensor_match()

    view._show_sensor_match(True, {}, stale)

    assert not view.filters_row.error.winfo_manager(), "the stale empty read must not raise a warning"


def test_switching_the_source_clears_the_old_verdict_at_once(view, monkeypatch):
    from iets_speed_control.sensors import LibreHardwareMonitorWebProvider

    monkeypatch.setattr(LibreHardwareMonitorWebProvider, "get_temperatures", lambda self: {"CPU": 50.0})
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)
    view._show_sensor_match(True, {})

    choose(row_named(view, "Sensors", "Temperature source"), "LibreHardwareMonitor (web server)")

    assert not view.filters_row.error.winfo_manager()
