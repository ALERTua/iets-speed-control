"""Curve maths and the curve editor widget.

The widget tests need a Tk display; they skip when none is available rather than failing.
"""

from types import SimpleNamespace

import pytest

from iets_speed_control.util.tools import calculate_dimmer_value, curve_to_ranges

DEFAULT_POINTS = [(40, 30), (50, 38), (60, 48), (70, 58), (80, 68), (90, 78), (100, 100)]


# --- curve <-> ranges ---------------------------------------------------------------------------


def test_ranges_cover_the_whole_temperature_span():
    ranges = curve_to_ranges([(40, 30), (100, 100)])

    assert ranges[0][0] == 0.0, "a shelf must start at 0 C"
    assert ranges[-1][1] >= 200, "a shelf must extend past any real temperature"


def test_shelves_hold_the_edge_percentages():
    ranges = curve_to_ranges([(40, 30), (90, 80)])

    assert calculate_dimmer_value(0, ranges) == 30
    assert calculate_dimmer_value(39, ranges) == 30
    assert calculate_dimmer_value(150, ranges) == 80


def test_percent_is_interpolated_between_points():
    ranges = curve_to_ranges([(40, 0), (80, 100)])

    assert calculate_dimmer_value(40, ranges) == 0
    assert calculate_dimmer_value(60, ranges) == 50
    assert calculate_dimmer_value(79, ranges) == 97


def test_points_at_the_same_temperature_do_not_divide_by_zero():
    ranges = curve_to_ranges([(50, 20), (50, 60), (70, 80)])

    assert all(low < high for low, high, _p0, _p1 in ranges)
    assert calculate_dimmer_value(60, ranges) is not None


def test_curve_needs_points():
    with pytest.raises(ValueError, match="at least one point"):
        curve_to_ranges([])


# --- the temperature axis -------------------------------------------------------------------------
#
# Broken at the knee: everything below it is squashed into a narrow band so the range worth editing
# gets the width. These are pure functions, so they need no Tk root.


def test_the_axis_spans_the_whole_range():
    from iets_speed_control.gui.curve_editor import TEMP_MAX, TEMP_MIN, temp_to_fraction

    assert temp_to_fraction(TEMP_MIN) == 0
    assert temp_to_fraction(TEMP_MAX) == 1


def test_the_cold_end_is_squashed():
    from iets_speed_control.gui.curve_editor import KNEE_FRACTION, TEMP_KNEE, temp_to_fraction

    assert temp_to_fraction(TEMP_KNEE) == pytest.approx(KNEE_FRACTION)
    assert KNEE_FRACTION < 0.2, "the point of the knee is that the cold end gets little width"


def test_the_interesting_range_keeps_most_of_the_width():
    from iets_speed_control.gui.curve_editor import TEMP_KNEE, temp_to_fraction

    assert temp_to_fraction(TEMP_KNEE) < 0.2
    assert 1 - temp_to_fraction(TEMP_KNEE) > 0.8


def test_the_axis_never_goes_backwards():
    from iets_speed_control.gui.curve_editor import temp_to_fraction

    fractions = [temp_to_fraction(temp) for temp in range(101)]

    assert fractions == sorted(fractions)
    assert len(set(fractions)) == len(fractions), "two temperatures must not land on the same spot"


def test_above_the_knee_the_axis_is_still_linear():
    """Precision matters most here, so this stretch must not be bent as a log scale would bend it."""
    from iets_speed_control.gui.curve_editor import temp_to_fraction

    steps = [temp_to_fraction(temp + 10) - temp_to_fraction(temp) for temp in range(30, 100, 10)]

    assert steps == pytest.approx([steps[0]] * len(steps))


@pytest.mark.parametrize("temp", [0, 1, 15, 29, 30, 31, 55, 85, 99, 100])
def test_a_temperature_survives_the_round_trip(temp):
    from iets_speed_control.gui.curve_editor import fraction_to_temp, temp_to_fraction

    assert fraction_to_temp(temp_to_fraction(temp)) == pytest.approx(temp)


def test_a_point_below_the_knee_is_still_on_the_plot(tk_root):
    """A curve whose first point sits at 1 C is normal; clipping the axis at 30 would hide it."""
    from iets_speed_control.gui.curve_editor import CurveEditor

    widget = CurveEditor(tk_root, points=[(1, 48), (65, 48), (92, 100)])
    widget.pack(fill="both", expand=True)
    tk_root.update()
    try:
        x0, _y0, x1, _y1 = widget._plot_box()
        x, _y = widget.to_pixels(1, 48)

        assert x0 < x < x1, "squashed is not the same as collapsed onto the axis"
        assert x - x0 < (x1 - x0) * 0.2, "it belongs in the squashed band, not out in the useful range"
        # The band still has to be usable: two cold temperatures cannot share one pixel column.
        assert widget.to_pixels(25, 48)[0] - widget.to_pixels(5, 48)[0] > 4
    finally:
        widget.destroy()
        tk_root.update()


def test_dragging_below_the_knee_still_lands_where_the_cursor_is(editor):
    editor.set_points([(5, 20), (60, 50), (90, 90)])
    editor._history.clear()

    grab(editor, 5, 20)
    x, y = editor.to_pixels(22, 20)
    editor.canvas.event_generate("<B1-Motion>", x=int(x), y=int(y))
    editor.canvas.event_generate("<ButtonRelease-1>")

    assert abs(editor.points[0][0] - 22) <= 1, f"expected to land near 22 C, got {editor.points[0]}"


# --- editor widget ------------------------------------------------------------------------------


@pytest.fixture
def editor(tk_root):
    from iets_speed_control.gui.curve_editor import CurveEditor

    widget = CurveEditor(tk_root, points=DEFAULT_POINTS)
    widget.pack(fill="both", expand=True)
    tk_root.update()
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


def press(widget, temp, pct):
    x, y = widget.to_pixels(temp, pct)
    widget.canvas.event_generate("<Button-1>", x=int(x), y=int(y))


def grab(widget, temp, pct):
    """Select a point and then take hold of it: dragging needs a press on an already-selected point.

    The second press calls the handler directly. Tk decides on its own whether a second click within
    the double-click interval is delivered as Button-1 or Double-Button-1, and a test should not
    depend on which side of that threshold it lands.
    """
    press(widget, temp, pct)
    x, y = widget.to_pixels(temp, pct)
    widget._on_press(SimpleNamespace(x=int(x), y=int(y)))


def drag_to(widget, temp, pct):
    x, y = widget.to_pixels(temp, pct)
    widget.canvas.event_generate("<B1-Motion>", x=int(x), y=int(y))
    widget.canvas.event_generate("<ButtonRelease-1>")


def percentages(widget):
    return [p for _t, p in widget.points]


def test_dragging_a_point_moves_it(editor):
    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)

    assert editor.points[2] != (60, 48)
    assert editor.selected == 2


def test_raising_a_point_pushes_the_ones_to_its_right(editor):
    grab(editor, *editor.points[1])
    drag_to(editor, 50, 88)

    raised = editor.points[1][1]
    assert raised > 80, f"the drag itself did not take effect: {editor.points[1]}"
    # Every point to the right started below 88 %, so each one must have been pulled up.
    assert all(pct >= raised for pct in percentages(editor)[2:-1])
    assert percentages(editor) == sorted(percentages(editor))


def test_lowering_a_point_pulls_the_ones_to_its_left(editor):
    grab(editor, *editor.points[4])
    drag_to(editor, 80, 12)

    lowered = editor.points[4][1]
    assert lowered < 20, f"the drag itself did not take effect: {editor.points[4]}"
    # Every point to the left started above 12 %, so each one must have been pulled down.
    assert all(pct <= lowered for pct in percentages(editor)[:4])
    assert percentages(editor) == sorted(percentages(editor))


def test_a_point_cannot_pass_its_neighbours(editor):
    grab(editor, *editor.points[3])
    drag_to(editor, 5, 58)  # far left, past three neighbours

    temperatures = [t for t, _p in editor.points]
    assert temperatures == sorted(temperatures)
    assert temperatures[3] > temperatures[2]
    assert temperatures[3] < 70, f"the drag itself did not take effect: {temperatures}"


def test_a_point_must_be_selected_before_it_can_be_dragged(editor):
    """One stray click used to move the curve; now the first click only picks the point."""
    before = editor.points[3]
    assert editor.selected != 3, "test setup: the point must start unselected"

    press(editor, *before)
    drag_to(editor, 5, 20)

    assert editor.selected == 3, "the click must still select"
    assert editor.points[3] == before, "an unselected point must not move"


def test_a_second_press_takes_hold_of_the_selected_point(editor):
    grab(editor, *editor.points[3])
    drag_to(editor, 68, 40)

    assert editor.points[3] != (70, 58), "a press on the already-selected point must arm the drag"


def test_a_quick_second_click_also_takes_hold(editor):
    """Tk delivers a fast second click as a double-click, which must not swallow the drag."""
    press(editor, *editor.points[3])
    x, y = editor.to_pixels(*editor.points[3])
    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))
    drag_to(editor, 68, 40)

    assert editor.points[3] != (70, 58)


def test_a_quick_double_click_on_an_unselected_point_does_not_move_it(editor):
    before = editor.points[3]
    x, y = editor.to_pixels(*before)

    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))
    drag_to(editor, 5, 20)

    assert editor.points[3] == before, "the point was not selected yet, so it must only be selected"
    assert editor.selected == 3


def test_selecting_another_point_does_not_move_the_previous_one(editor):
    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)
    moved = editor.points[2]

    press(editor, *editor.points[5])
    drag_to(editor, 88, 20)

    assert editor.points[2] == moved
    assert editor.points[5] == (90, 78)


# --- undo ---------------------------------------------------------------------------------------


def undo(widget):
    """Press Ctrl+Z on the canvas. Tk routes key events to whatever holds the focus, so a test that
    has not clicked the canvas first has to give it focus explicitly."""
    widget.canvas.focus_force()
    widget.update()
    widget.canvas.event_generate("<Control-z>")


def test_undo_restores_the_curve_after_a_drag(editor):
    before = list(editor.points)
    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)
    assert editor.points != before, "test setup: the drag must have changed something"

    undo(editor)

    assert editor.points == before


def test_a_whole_drag_is_one_undo_step(editor):
    """Undoing must not walk back through every mouse-motion event."""
    before = list(editor.points)
    grab(editor, *editor.points[2])
    for temp in (61, 62, 63, 64):
        x, y = editor.to_pixels(temp, 55)
        editor.canvas.event_generate("<B1-Motion>", x=int(x), y=int(y))
    editor.canvas.event_generate("<ButtonRelease-1>")

    undo(editor)

    assert editor.points == before


def test_undo_restores_a_removed_point(editor):
    before = list(editor.points)
    press(editor, *editor.points[3])
    editor.canvas.event_generate("<Delete>")
    assert len(editor.points) == len(before) - 1

    undo(editor)

    assert editor.points == before


def test_undo_removes_an_inserted_point(editor):
    before = list(editor.points)
    x, y = editor.to_pixels(45, 34)
    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))
    assert len(editor.points) == len(before) + 1

    undo(editor)

    assert editor.points == before


def test_undo_restores_a_typed_value(editor):
    before = list(editor.points)
    editor.selected = 1
    editor.update_selected(temp=52, pct=95)

    undo(editor)

    assert editor.points == before


def test_undo_steps_back_one_change_at_a_time(editor):
    first = list(editor.points)
    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)
    second = list(editor.points)
    press(editor, *editor.points[5])
    press(editor, *editor.points[5])
    drag_to(editor, 88, 90)

    undo(editor)
    assert editor.points == second

    undo(editor)
    assert editor.points == first


def test_undo_does_nothing_with_no_history(editor):
    before = list(editor.points)

    assert editor.undo() is False
    assert editor.points == before


def test_a_click_that_moves_nothing_leaves_no_undo_step(editor):
    grab(editor, *editor.points[2])
    editor.canvas.event_generate("<ButtonRelease-1>")

    assert editor.undo() is False, "a press without movement is not a change"


def test_a_drag_that_changes_nothing_leaves_no_undo_step(editor):
    """A point held against a neighbour it already touches produces motion but no change."""
    editor.set_points([(40, 30), (41, 30), (90, 90)])
    editor._history.clear()  # set_points is itself undoable; this test is about the drag
    editor.selected = 1

    grab(editor, 41, 30)
    x, y = editor.to_pixels(20, 30)  # far left, where the neighbour at 40 blocks the way
    editor.canvas.event_generate("<B1-Motion>", x=int(x), y=int(y))
    editor.canvas.event_generate("<ButtonRelease-1>")

    assert editor.points[1] == (41, 30), "test setup: the clamp must have held the point in place"
    assert editor.undo() is False, "an undo step that restores the same curve does nothing when used"


def test_undo_tells_the_controller_about_the_restored_curve(editor):
    seen = []
    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)
    editor.on_change = lambda points, _selected: seen.append(list(points))

    undo(editor)

    assert seen, "without notifying, the fan would keep following the curve that was undone"
    assert seen[-1] == editor.points


def test_the_history_is_bounded(editor):
    from iets_speed_control.gui.curve_editor import MAX_UNDO

    for pct in range(MAX_UNDO + 10):
        editor.selected = 1
        editor.update_selected(pct=pct % 100)

    assert len(editor._history) <= MAX_UNDO


def test_undo_while_typing_elsewhere_does_not_touch_the_curve(editor, tk_root):
    """The binding is on the canvas, so Ctrl+Z with the focus in an entry must not revert the curve."""
    import customtkinter as ctk

    grab(editor, *editor.points[2])
    drag_to(editor, 62, 55)
    after_drag = list(editor.points)

    elsewhere = ctk.CTkEntry(tk_root)
    elsewhere.pack()
    elsewhere._entry.focus_force()
    tk_root.update()
    try:
        elsewhere._entry.event_generate("<Control-z>")
        tk_root.update()

        assert editor.points == after_drag
    finally:
        elsewhere.destroy()
        tk_root.update()


def test_double_click_inserts_a_point(editor):
    # Tk refuses to synthesize the Double modifier, so the handler is called directly.
    before = len(editor.points)
    x, y = editor.to_pixels(45, 34)
    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))

    assert len(editor.points) == before + 1
    assert [t for t, _p in editor.points] == sorted(t for t, _p in editor.points)


def test_double_click_on_an_existing_point_does_not_insert(editor):
    before = len(editor.points)
    x, y = editor.to_pixels(*editor.points[2])
    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))

    assert len(editor.points) == before


def test_right_click_removes_a_point(editor):
    before = len(editor.points)
    x, y = editor.to_pixels(*editor.points[3])
    editor.canvas.event_generate("<Button-3>", x=int(x), y=int(y))

    assert len(editor.points) == before - 1


def test_two_points_are_never_removed(editor):
    editor.set_points([(40, 30), (90, 90)])

    for _ in range(3):
        x, y = editor.to_pixels(*editor.points[0])
        editor.canvas.event_generate("<Button-3>", x=int(x), y=int(y))

    assert len(editor.points) == 2


def test_numeric_entry_applies_the_same_rules_as_dragging(editor):
    editor.selected = 1
    editor.update_selected(temp=52, pct=95)

    assert editor.points[1] == (52.0, 95.0)
    assert all(pct >= 95 for pct in percentages(editor)[2:]), "cascade must apply to typed values too"


def test_on_change_reports_the_selected_point(editor):
    seen = []
    editor.on_change = lambda points, selected: seen.append(points[selected])
    press(editor, *editor.points[0])

    assert seen and seen[-1] == editor.points[0]


# --- Delete key ---------------------------------------------------------------------------------


def test_delete_removes_the_selected_point(editor):
    before = len(editor.points)
    press(editor, *editor.points[3])
    removed = editor.points[3]

    editor.canvas.event_generate("<Delete>")

    assert len(editor.points) == before - 1
    assert removed not in editor.points


def test_delete_keeps_the_selection_valid(editor):
    before = len(editor.points)
    press(editor, *editor.points[-1])  # deleting the last point would leave the index dangling

    editor.canvas.event_generate("<Delete>")

    assert len(editor.points) == before - 1, "the point was not removed at all"
    assert editor.selected == len(editor.points) - 1
    assert 0 <= editor.selected < len(editor.points)


def test_delete_refuses_to_go_below_two_points(editor):
    editor.set_points([(40, 30), (90, 90)])

    for _ in range(3):
        editor.canvas.event_generate("<Delete>")

    assert len(editor.points) == 2


def test_the_canvas_can_take_keyboard_focus(editor, tk_root):
    """takefocus is the whole mechanism: without it a click leaves focus elsewhere and Delete is lost."""
    import customtkinter as ctk

    assert editor.canvas.cget("takefocus") in (1, "1", True)

    elsewhere = ctk.CTkEntry(tk_root)
    elsewhere.pack()
    elsewhere.focus_set()
    tk_root.update()
    assert editor.canvas.focus_get() is not editor.canvas, "test setup: focus should start elsewhere"

    try:
        press(editor, *editor.points[1])
        tk_root.update()

        assert editor.canvas.focus_get() is editor.canvas, "clicking must bring focus to the canvas"
    finally:
        elsewhere.destroy()
        tk_root.update()


def test_delete_elsewhere_does_not_touch_the_curve(editor, tk_root):
    """The binding is on the canvas, so Delete while typing in an entry must not eat a point."""
    before = len(editor.points)

    tk_root.event_generate("<Delete>")

    assert len(editor.points) == before


# --- whole units -------------------------------------------------------------------------------


def test_points_are_whole_numbers_on_load(tk_root):
    from iets_speed_control.gui.curve_editor import CurveEditor

    widget = CurveEditor(tk_root, points=[(40.4, 30.6), (70.5, 55.2)])
    try:
        assert widget.points == [(40, 31), (70, 55)]
        assert all(isinstance(v, int) for point in widget.points for v in point)
    finally:
        widget.destroy()


def test_dragging_lands_on_whole_units(editor):
    grab(editor, *editor.points[2])
    drag_to(editor, 63.7, 52.4)

    temp, pct = editor.points[2]
    assert temp == int(temp) and pct == int(pct), (temp, pct)


def test_typed_fractions_are_rounded(editor):
    editor.selected = 1
    editor.update_selected(temp=52.6, pct=41.2)

    assert editor.points[1] == (53, 41)


def test_inserted_points_are_whole(editor):
    x, y = editor.to_pixels(45.7, 33.3)
    editor._on_double_click(SimpleNamespace(x=int(x), y=int(y)))

    assert all(isinstance(v, int) for point in editor.points for v in point)
