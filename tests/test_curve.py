"""Curve maths and the curve editor widget.

The widget tests need a Tk display; they skip when none is available rather than failing.
"""

from types import SimpleNamespace

import pytest

from iets_speed_control.util.tools import calculate_dimmer_value, curve_to_ranges, ranges_to_curve

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


def test_ranges_to_curve_drops_synthetic_shelves():
    points = ranges_to_curve(curve_to_ranges([(40, 30), (90, 80)]))

    assert points == [(40.0, 30.0), (90.0, 80.0)]


def test_ranges_to_curve_reads_the_legacy_string_form():
    points = ranges_to_curve("(1, 65, 48, 48), (66, 85, 49, 54)")

    assert points[0] == (1.0, 48.0)
    assert (85.0, 54.0) in points


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


def drag_to(widget, temp, pct):
    x, y = widget.to_pixels(temp, pct)
    widget.canvas.event_generate("<B1-Motion>", x=int(x), y=int(y))
    widget.canvas.event_generate("<ButtonRelease-1>")


def percentages(widget):
    return [p for _t, p in widget.points]


def test_dragging_a_point_moves_it(editor):
    press(editor, *editor.points[2])
    drag_to(editor, 62, 55)

    assert editor.points[2] != (60, 48)
    assert editor.selected == 2


def test_raising_a_point_pushes_the_ones_to_its_right(editor):
    press(editor, *editor.points[1])
    drag_to(editor, 50, 88)

    raised = editor.points[1][1]
    assert raised > 80, f"the drag itself did not take effect: {editor.points[1]}"
    # Every point to the right started below 88 %, so each one must have been pulled up.
    assert all(pct >= raised for pct in percentages(editor)[2:-1])
    assert percentages(editor) == sorted(percentages(editor))


def test_lowering_a_point_pulls_the_ones_to_its_left(editor):
    press(editor, *editor.points[4])
    drag_to(editor, 80, 12)

    lowered = editor.points[4][1]
    assert lowered < 20, f"the drag itself did not take effect: {editor.points[4]}"
    # Every point to the left started above 12 %, so each one must have been pulled down.
    assert all(pct <= lowered for pct in percentages(editor)[:4])
    assert percentages(editor) == sorted(percentages(editor))


def test_a_point_cannot_pass_its_neighbours(editor):
    press(editor, *editor.points[3])
    drag_to(editor, 5, 58)  # far left, past three neighbours

    temperatures = [t for t, _p in editor.points]
    assert temperatures == sorted(temperatures)
    assert temperatures[3] > temperatures[2]
    assert temperatures[3] < 70, f"the drag itself did not take effect: {temperatures}"


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
