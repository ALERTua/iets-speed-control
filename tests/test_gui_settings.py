"""The settings panel: every configuration key it now edits, and what each edit applies to.

Two properties matter more than the widgets themselves:
  * a value the app could not start with must never reach the live configuration, and
  * a key whose change needs code (the tracked sensors, the smoothing window, the serial port) must
    actually reach that code, not just the config object.

Tk delivers synthesized events only to mapped widgets, and only one section is packed at a time, so
every test that types into a row shows that section first. Skipping this turns the "bad input is
refused" tests into assertions that nothing happened, which is true when nothing happened at all.
"""

from dataclasses import fields

import pytest

from iets_speed_control.util import config as cfg
from iets_speed_control.util.config import CONFIG
from iets_speed_control.util.tracking import SourceState

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
def web_server_rows(view, monkeypatch):
    """Show the web-server rows, which exist only while lhm-web is tracked.

    A hidden row has no width and takes no typing.
    """
    track(view, monkeypatch, {"lhm-web": []})


def test_a_bad_web_server_timeout_is_refused(view, web_server_rows):
    before = CONFIG.sensors.lhm_web.timeout

    row = edit(view, "Sensors", "Web server timeout", "0")

    assert "greater than zero" in row.error.cget("text")
    assert CONFIG.sensors.lhm_web.timeout == before


# --- what an edit applies to ---------------------------------------------------------------------


def test_changing_the_smoothing_window_rebuilds_the_smoothers(view):
    before = view.controller._smoother

    edit(view, "Control", "Smoothing window", "9")

    assert CONFIG.control.temp_window == 9
    assert view.controller.temp_window == 9
    assert view.controller._smoother is not before, "a resized window needs a new smoother"
    assert view.controller._smoother.window == 9


def test_the_default_speed_is_written(view):
    edit(view, "Control", "Default speed", "35")

    assert CONFIG.control.default_speed == 35


def test_a_default_speed_beyond_full_speed_is_refused(view):
    before = CONFIG.control.default_speed

    row = edit(view, "Control", "Default speed", "150")

    assert "0-100" in row.error.cget("text")
    assert CONFIG.control.default_speed == before
    assert row.entry.get() == str(before), "the entry must show what is actually stored"


def test_a_fractional_default_speed_is_refused(view):
    before = CONFIG.control.default_speed

    row = edit(view, "Control", "Default speed", "40.5")

    assert "whole number" in row.error.cget("text")
    assert CONFIG.control.default_speed == before


def test_asking_a_row_for_a_combo_it_does_not_have_is_an_error(view):
    with pytest.raises(TypeError, match="Baud rate"):
        row_named(view, "Device", "Baud rate").require_combo()


def test_a_combo_row_hands_back_its_combo(view):
    row = row_named(view, "Device", "Serial port")

    assert row.require_combo() is row.combo


def test_the_web_server_rows_appear_only_while_that_source_is_tracked(view, monkeypatch):
    track(view, monkeypatch, {"aida64": []})
    assert not row_named(view, "Sensors", "Web server URL").text.winfo_manager()

    track(view, monkeypatch, {"aida64": [], "lhm-web": []})

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


def test_no_description_is_cut_off(view, tk_root, monkeypatch):
    """Descriptions have to fit the column they are in, because nothing tells you when they do not.

    Grid sizes the control column to the widest control in the whole card, so one wide control
    narrows every description in that card, and the surplus is silently clipped mid-word. Wrapping is
    not a way out: a CTkLabel keeps its one-line height and simply does not draw the second line.

    Measured at the narrowest window the app allows, since that is where the columns are tightest.
    """
    from iets_speed_control.gui import sensor_picker
    from iets_speed_control.gui.theme import RAIL_EXPANDED_WIDTH, WINDOW_MIN_SIZE

    # The web-server rows show only while lhm-web is tracked, and the admin note only on a source that
    # needs rights this process lacks. No real setup shows the note on every block, so it is forced here.
    monkeypatch.setattr(sensor_picker, "lacks_admin_rights", lambda provider: True)
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)
    track(view, monkeypatch, {"lhm-web": [], "lenovo-wmi": []})
    view._apply_admin_note()
    picker = view.sensor_picker
    assert all(block.admin_note.winfo_manager() for block in picker.blocks.values()), "test setup"

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

            if name == "Sensors":
                for block in picker.blocks.values():
                    room = block.winfo_width()
                    needed = block.admin_note.winfo_reqwidth()
                    if needed > room:
                        too_wide.append(f"Sensors/{block.name} admin note: needs {needed}px, has {room}px")
                room = picker.winfo_width()
                needed = picker.footer.winfo_reqwidth()
                if needed > room:
                    too_wide.append(f"Sensors/Add source: needs {needed}px, has {room}px")
    finally:
        tk_root.geometry(was)
        tk_root.update()

    assert not too_wide, f"at {content_width}px these are cut off:\n" + "\n".join(too_wide)


# --- the sensor picker ----------------------------------------------------------------------------


READINGS = {
    "aida64": {"CPU Package": 61.0, "CPU Core #1": 63.5, "GPU Hot Spot": 44.0},
    "lhm": {"Intel Core i7/CPU Package": 60.0},
    "lhm-web": {"Intel Core i7/CPU Package": 60.0},
    "lenovo-wmi": {"CPU": 58.0, "GPU": 45.0},
    "nvidia": {"GPU Core": 47.0},
}


class FakeSource:
    """Any object with a name and get_temperatures() is a source."""

    def __init__(self, name, readings):
        self.name = name
        self.readings = dict(readings)

    def get_temperatures(self):
        return dict(self.readings)


@pytest.fixture(autouse=True)
def fake_sources(view, monkeypatch):
    """A fake for every source the configuration accepts, so no test reaches WMI, NVAPI or the network.

    The controller builds a source from these whenever sensors.tracked names it.
    """
    fakes = {name: FakeSource(name, readings) for name, readings in READINGS.items()}
    for name, fake in fakes.items():
        monkeypatch.setitem(view.controller._given_sources, name, fake)
    return fakes


def track(view, monkeypatch, tracked):
    """Store a tracked set the way a hand-edited file would, and let the card catch up."""
    monkeypatch.setattr(CONFIG.sensors, "tracked", tracked)
    view._apply_tracked()


def answer(view, readings, state=SourceState.OK):
    """Hand the picker one read, as a background read that just finished would: {source: readings}."""
    view._show_source_reads(True, {name: (state, values) for name, values in readings.items()}, view._read_request)


def pump(tk_root, until):
    """Let Tk run its `after` jobs until `until()` holds, or about a second has passed."""
    for _ in range(60):
        tk_root.update()
        if until():
            return
        tk_root.after(20, tk_root.quit)
        tk_root.mainloop()


def line(view, source, label):
    found = view.sensor_picker.blocks[source].line_for(label)
    assert found is not None, f"no line {label!r} in {source}"
    return found


def test_with_nothing_tracked_the_picker_says_what_that_means(view):
    from iets_speed_control.gui.sensor_picker import NOTHING_TRACKED
    from iets_speed_control.sensors import PROVIDER_LABELS

    picker = view.sensor_picker
    assert picker.blocks == {}
    assert picker.empty_label.winfo_manager()
    assert picker.empty_label.cget("text") == NOTHING_TRACKED
    assert picker.add_menu.cget("values") == list(PROVIDER_LABELS.values())


def test_adding_a_source_tracks_it_with_nothing_picked(view, fake_sources):
    picker = view.sensor_picker
    picker.add_menu.set("NVIDIA driver (NVAPI)")

    picker.add_button.cget("command")()

    assert CONFIG.sensors.tracked == {"nvidia": []}, "the file keeps the code name, not the label"
    assert view.controller.sources == {"nvidia": fake_sources["nvidia"]}, "the controller must read it too"
    assert list(picker.blocks) == ["nvidia"]
    assert "NVIDIA driver (NVAPI)" not in picker.add_menu.cget("values"), "a source is tracked only once"
    assert not picker.empty_label.winfo_manager()


def test_a_source_added_while_the_card_is_shown_is_read_at_once(view, tk_root):
    view.show("Sensors")
    picker = view.sensor_picker
    picker.add_menu.set("NVIDIA driver (NVAPI)")

    picker.add_button.cget("command")()
    pump(tk_root, lambda: picker.blocks["nvidia"].line_for("GPU Core") is not None)

    assert line(view, "nvidia", "GPU Core").value.cget("text") == "47 °C"


def test_a_block_lists_every_sensor_its_source_reports_with_its_value(view, monkeypatch):
    track(view, monkeypatch, {"aida64": ["CPU Package"]})

    answer(view, {"aida64": READINGS["aida64"]})

    block = view.sensor_picker.blocks["aida64"]
    assert [item.label for item in block.lines] == ["CPU Package", "CPU Core #1", "GPU Hot Spot"]
    assert [item.checked for item in block.lines] == [True, False, False], "checked means tracked"
    assert line(view, "aida64", "CPU Core #1").value.cget("text") == "63.5 °C"


def test_the_background_read_reaches_the_picker(view, tk_root, monkeypatch, fake_sources):
    """The read runs off the Tk thread through read_source, which also drops implausible values."""
    fake_sources["aida64"].readings["Not fitted"] = 0.0
    track(view, monkeypatch, {"aida64": []})

    view._read_sources()
    pump(tk_root, lambda: view.sensor_picker.blocks["aida64"].lines)

    labels = [item.label for item in view.sensor_picker.blocks["aida64"].lines]
    assert labels == ["CPU Package", "CPU Core #1", "GPU Hot Spot"]


def test_checking_a_sensor_tracks_it(view, monkeypatch, fake_sources):
    track(view, monkeypatch, {"aida64": []})
    answer(view, {"aida64": READINGS["aida64"]})

    line(view, "aida64", "GPU Hot Spot").checkbox.toggle()

    assert CONFIG.sensors.tracked == {"aida64": ["GPU Hot Spot"]}
    assert view.controller.monitoring, "the controller decides from the stored set whether it monitors"
    assert view.controller.sources == {"aida64": fake_sources["aida64"]}
    assert line(view, "aida64", "GPU Hot Spot").checked


def test_unchecking_a_sensor_stops_tracking_it(view, monkeypatch):
    track(view, monkeypatch, {"aida64": ["CPU Package", "GPU Hot Spot"]})
    answer(view, {"aida64": READINGS["aida64"]})

    line(view, "aida64", "CPU Package").checkbox.toggle()

    assert CONFIG.sensors.tracked == {"aida64": ["GPU Hot Spot"]}
    assert not line(view, "aida64", "CPU Package").checked


def test_unchecking_the_last_sensor_keeps_the_source(view, monkeypatch):
    """A source with nothing picked stays listed, so its sensors are still there to choose from."""
    track(view, monkeypatch, {"aida64": ["CPU Package"]})
    answer(view, {"aida64": READINGS["aida64"]})

    line(view, "aida64", "CPU Package").checkbox.toggle()

    assert CONFIG.sensors.tracked == {"aida64": []}
    assert not view.controller.monitoring
    assert "aida64" in view.controller.sources


def test_a_tracked_sensor_the_source_no_longer_reports_stays_checked(view, monkeypatch):
    from iets_speed_control.gui.sensor_picker import NO_READING
    from iets_speed_control.gui.theme import ERROR_COLOR

    track(view, monkeypatch, {"aida64": ["Gone", "CPU Package"]})

    answer(view, {"aida64": READINGS["aida64"]})

    gone = line(view, "aida64", "Gone")
    assert gone.checked
    assert gone.value.cget("text") == NO_READING
    assert gone.value.cget("text_color") == ERROR_COLOR


def test_a_tracked_sensor_the_source_no_longer_reports_can_be_unchecked(view, monkeypatch):
    track(view, monkeypatch, {"aida64": ["Gone", "CPU Package"]})
    answer(view, {"aida64": READINGS["aida64"]})

    line(view, "aida64", "Gone").checkbox.toggle()

    assert CONFIG.sensors.tracked == {"aida64": ["CPU Package"]}
    assert view.sensor_picker.blocks["aida64"].line_for("Gone") is None, "nothing is left to show it for"


def test_a_sleeping_source_says_asleep_without_an_alarm(view, monkeypatch):
    from iets_speed_control.gui.sensor_picker import ASLEEP
    from iets_speed_control.gui.theme import ERROR_COLOR

    track(view, monkeypatch, {"nvidia": ["GPU Core"]})

    answer(view, {"nvidia": {}}, SourceState.ASLEEP)

    block = view.sensor_picker.blocks["nvidia"]
    assert line(view, "nvidia", "GPU Core").value.cget("text") == ASLEEP
    assert line(view, "nvidia", "GPU Core").value.cget("text_color") != ERROR_COLOR
    assert block.state_label.cget("text") == "asleep"
    assert block.state_label.cget("text_color") != ERROR_COLOR


def test_a_silent_source_says_so_in_its_header(view, monkeypatch):
    from iets_speed_control.gui.theme import ERROR_COLOR

    track(view, monkeypatch, {"lhm-web": ["CPU"]})

    answer(view, {"lhm-web": {}}, SourceState.SILENT)

    block = view.sensor_picker.blocks["lhm-web"]
    assert block.state_label.cget("text") == "no answer"
    assert block.state_label.cget("text_color") == ERROR_COLOR


def test_a_source_that_answers_again_drops_its_state(view, monkeypatch):
    track(view, monkeypatch, {"aida64": []})
    answer(view, {"aida64": {}}, SourceState.SILENT)

    answer(view, {"aida64": READINGS["aida64"]})

    assert view.sensor_picker.blocks["aida64"].state_label.cget("text") == ""


def test_removing_a_source_stops_reading_it(view, monkeypatch):
    track(view, monkeypatch, {"aida64": ["CPU Package"], "nvidia": ["GPU Core"]})
    removed = view.sensor_picker.blocks["aida64"]

    removed.remove_button.cget("command")()

    assert CONFIG.sensors.tracked == {"nvidia": ["GPU Core"]}
    assert list(view.controller.sources) == ["nvidia"], "the controller must stop reading it too"
    assert list(view.sensor_picker.blocks) == ["nvidia"]
    assert not removed.winfo_exists()
    assert "AIDA64" in view.sensor_picker.add_menu.cget("values"), "it can be added again"


def test_the_blocks_follow_the_order_of_the_tracked_set(view, monkeypatch):
    from iets_speed_control.gui.sensor_picker import SourceBlock

    track(view, monkeypatch, {"nvidia": [], "aida64": []})

    packed = [widget for widget in view.sensor_picker.blocks_frame.pack_slaves() if isinstance(widget, SourceBlock)]
    assert [block.name for block in packed] == ["nvidia", "aida64"]


def test_a_block_keeps_its_lines_while_its_sensors_stay_the_same(view, monkeypatch):
    """A read every two seconds; rebuilding the check boxes each time would make the card crawl."""
    track(view, monkeypatch, {"aida64": ["CPU Package"]})
    answer(view, {"aida64": READINGS["aida64"]})
    before = [(item.checkbox, item.value) for item in view.sensor_picker.blocks["aida64"].lines]

    answer(view, {"aida64": {**READINGS["aida64"], "CPU Package": 70.0}})

    after = [(item.checkbox, item.value) for item in view.sensor_picker.blocks["aida64"].lines]
    assert after == before
    assert line(view, "aida64", "CPU Package").value.cget("text") == "70 °C"


def test_a_read_that_finishes_after_a_newer_one_started_is_dropped(view, monkeypatch):
    """A slow read of the old set of sources must not paint its values over the new one."""
    track(view, monkeypatch, {"aida64": []})
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)
    view._read_sources()
    stale = view._read_request
    view._read_sources()

    view._show_source_reads(True, {"aida64": (SourceState.OK, READINGS["aida64"])}, stale)

    assert view.sensor_picker.blocks["aida64"].lines == [], "the stale answer must not be shown"


def test_an_answer_that_arrives_after_the_card_was_left_is_dropped(view, monkeypatch):
    track(view, monkeypatch, {"aida64": []})
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)
    view.show("Sensors")
    request = view._read_request

    view.show("Control")
    view._show_source_reads(True, {"aida64": (SourceState.OK, READINGS["aida64"])}, request)

    assert view.sensor_picker.blocks["aida64"].lines == []


def test_the_sources_are_read_only_while_the_card_is_shown(view, monkeypatch):
    monkeypatch.setattr(view, "_in_background", lambda work, done: None)

    view.show("Sensors")
    assert view._reads_job is not None, "the values must keep coming while the card is open"

    view.show("Control")
    assert view._reads_job is None


def test_a_refused_tracked_set_is_reported_and_not_stored(view):
    view.sensor_picker._propose({"nonsense": []})

    assert CONFIG.sensors.tracked == {}
    assert view.sensor_picker.error_label.winfo_manager()
    assert "no such source" in view.sensor_picker.error_label.cget("text")


def test_reset_stops_tracking_everything(view, monkeypatch):
    track(view, monkeypatch, {"aida64": ["CPU Package"], "lhm-web": []})
    assert row_named(view, "Sensors", "Web server URL").text.winfo_manager(), "test setup"

    view._reset_section(view.sections["Sensors"])

    assert CONFIG.sensors.tracked == {}
    assert view.controller.sources == {}, "the controller must stop reading what the card no longer shows"
    assert view.sensor_picker.blocks == {}
    assert view.sensor_picker.empty_label.winfo_manager()
    assert not row_named(view, "Sensors", "Web server URL").text.winfo_manager()


@pytest.fixture
def unelevated(monkeypatch):
    from iets_speed_control.sensors import base

    monkeypatch.setattr(base, "is_elevated", lambda: False)


def test_a_source_that_needs_admin_rights_says_so_on_its_block(view, monkeypatch, unelevated):
    from iets_speed_control.gui.sensor_picker import ADMIN_NOTE
    from iets_speed_control.gui.theme import ERROR_COLOR

    track(view, monkeypatch, {"lenovo-wmi": [], "aida64": []})

    note = view.sensor_picker.blocks["lenovo-wmi"].admin_note
    assert note.winfo_manager()
    assert note.cget("text") == ADMIN_NOTE
    assert note.cget("text_color") == ERROR_COLOR
    assert not view.sensor_picker.blocks["aida64"].admin_note.winfo_manager()


def test_an_elevated_app_shows_no_admin_note(view, monkeypatch):
    from iets_speed_control.sensors import base

    monkeypatch.setattr(base, "is_elevated", lambda: True)

    track(view, monkeypatch, {"lenovo-wmi": []})

    assert not view.sensor_picker.blocks["lenovo-wmi"].admin_note.winfo_manager()


def test_the_picker_does_not_widen_the_control_column(view):
    """The picker spans the card; its check boxes beside the descriptions would squeeze them all."""
    assert view.sensor_picker.grid_info()["columnspan"] == 2


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
