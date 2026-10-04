"""Guards on the GUI's threading model and the temperature-history graph.

The crash these protect against: the controller calls its callbacks from the asyncio thread, and
touching a Tk widget from there kills the process with a Windows access violation. Regression is
silent from Python's side -- no exception, just a dead process -- so the seam is asserted directly.
"""

import queue
import threading

import pytest

from iets_speed_control.util.filters import Selection
from iets_speed_control.util.tools import calculate_dimmer_value, curve_to_ranges


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def history(tk_root, clock):
    from iets_speed_control.gui.history import TemperatureHistory

    widget = TemperatureHistory(tk_root, window_seconds=600, clock=clock)
    widget.pack(fill="x")
    tk_root.update()
    try:
        yield widget
    finally:
        widget.destroy()
        tk_root.update()


# --- history window -----------------------------------------------------------------------------


def test_default_window_is_ten_minutes():
    from iets_speed_control.gui.theme import HISTORY_WINDOW_SECONDS

    assert HISTORY_WINDOW_SECONDS == 600


def test_timeline_choices_are_labelled_readably():
    from iets_speed_control.gui.settings import SPAN_LABELS, format_span

    assert format_span(60) == "1 minute", "singular must not read '1 minutes'"
    assert format_span(600) == "10 minutes"
    assert format_span(30) == "30 seconds"
    assert SPAN_LABELS["10 minutes"] == 600


def test_samples_older_than_the_window_are_dropped(history, clock):
    for _ in range(5):
        history.add(60)
        clock.advance(100)  # 5 samples spanning 400 s

    assert len(history.samples) == 5

    clock.advance(500)  # now the oldest are beyond the 600 s window
    history.add(60)

    ages = [clock.now - stamp for stamp, _value in history.samples]
    assert all(age <= history.window_seconds for age in ages), ages


def test_ten_minutes_of_samples_are_all_retained(history, clock):
    """The complaint that started this: the graph only showed the last minute."""
    for _ in range(120):  # 10 minutes at a 5 s cadence
        history.add(60)
        clock.advance(5)

    span = history.samples[-1][0] - history.samples[0][0]
    assert span >= 570, f"expected roughly ten minutes of history, got {span} s"


def test_shrinking_the_window_drops_what_no_longer_fits(history, clock):
    for _ in range(10):
        history.add(60)
        clock.advance(60)  # 10 minutes of samples

    history.set_window(120)

    ages = [clock.now - stamp for stamp, _value in history.samples]
    assert all(age <= 120 for age in ages), ages


def test_widening_the_window_keeps_existing_samples(history, clock):
    for _ in range(5):
        history.add(60)
        clock.advance(10)

    before = len(history.samples)
    history.set_window(3600)

    assert len(history.samples) == before


def test_empty_and_single_sample_histories_draw_without_error(history):
    history.redraw()
    history.add(55)
    history.redraw()

    assert len(history.samples) == 1


def test_long_histories_are_decimated_for_drawing(history, clock):
    for _ in range(4000):
        history.add(60)
        clock.advance(0.1)

    path = history._series(width=300)

    # Two coordinates per point, capped at POINTS_PER_PIXEL per pixel.
    assert len(path) // 2 <= 300 * 2 + 1, len(path) // 2


def test_span_label_reads_in_minutes(history):
    history.set_window(600)
    assert history.span_label() == "10 min"

    history.set_window(30)
    assert history.span_label() == "30 s"


# --- threading seam -----------------------------------------------------------------------------


def test_controller_callbacks_never_touch_widgets(tk_root):
    """A callback arriving on the asyncio thread must only enqueue data.

    The widget is replaced with an object that explodes on any attribute access, so any widget call
    inside the callback path fails the test instead of crashing the interpreter at runtime.
    """
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()

    class Explodes:
        def __getattr__(self, name):
            raise AssertionError(f"widget touched from a worker thread: .{name}")

    panel.history = Explodes()
    panel.value_labels = {"Max": Explodes(), "Fan": Explodes()}
    panel.status_label = Explodes()

    errors = []

    def worker():
        try:
            panel.controller._notify_temps()
            panel.controller._notify_speed()
            panel.controller._notify_status()
        except BaseException as e:  # noqa: BLE001 -- the assertion itself must reach the test
            errors.append(e)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert not errors, errors
    assert panel._updates.qsize() == 3, "each callback must leave exactly one queued update"
    panel.destroy()


def test_drain_applies_queued_updates(tk_root):
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()

    panel._post("temps", (71, Selection()))
    panel._post("speed", (42,))
    panel._drain()
    tk_root.update()

    assert "71" in panel.value_labels["Max"].cget("text")
    assert "42" in panel.value_labels["Fan"].cget("text")
    assert len(panel.history.samples) == 1
    panel.destroy()


def test_drain_keeps_every_reading_for_the_graph(tk_root):
    """Labels only need the newest value, but the graph must not lose samples to coalescing."""
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()

    for value in (60, 62, 64, 66):
        panel._post("temps", (value, Selection()))
    panel._drain()

    assert len(panel.history.samples) == 4
    assert "66" in panel.value_labels["Max"].cget("text")
    panel.destroy()


def test_a_drain_redraws_the_graph_once_for_the_whole_batch(tk_root):
    """Redrawing per reading rebuilt the whole canvas a dozen times to show the last of them."""
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()
    redraws = []
    panel.history.redraw = lambda: redraws.append(1)

    for cpu in (60, 62, 64, 66, 68):
        panel._post("temps", (cpu, Selection()))
    panel._drain()

    assert len(panel.history.samples) == 5, "every reading still has to be recorded"
    assert redraws == [1], f"one batch, one redraw; got {len(redraws)}"
    panel.destroy()


def test_a_drain_with_nothing_new_does_not_redraw(tk_root):
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()
    redraws = []
    panel.history.redraw = lambda: redraws.append(1)

    panel._drain()

    assert redraws == [], "an idle poll ten times a second must not repaint the graph"
    panel.destroy()


def test_queue_is_unbounded_enough_to_survive_a_stalled_ui(tk_root):
    from iets_speed_control.controller import SpeedController
    from iets_speed_control.gui.status import StatusPanel

    panel = StatusPanel(tk_root, SpeedController())
    panel.stop_polling()

    for index in range(5000):
        panel._post("temps", (index % 100, Selection()))

    assert isinstance(panel._updates, queue.Queue)
    assert panel._updates.qsize() == 5000
    panel.destroy()


# --- wire format --------------------------------------------------------------------------------


def test_dimmer_value_is_always_an_integer():
    """Curve points are floats; the device must still receive "Dimmer 49", not "Dimmer 49.0"."""
    ranges = curve_to_ranges([(40.0, 25.5), (70.0, 55.5), (90.0, 100.0)])

    for temperature in range(0, 120, 3):
        value = calculate_dimmer_value(temperature, ranges)
        assert isinstance(value, int), f"{temperature} C gave {value!r}"


def test_a_fresh_history_says_it_is_collecting(history, clock):
    """A ten-minute window starts as a sliver at the right edge; it must not read as broken."""
    history.add(60)
    clock.advance(2)
    history.add(61)

    assert history.filled_fraction() < 0.05


def test_a_full_window_is_not_marked_as_collecting(history, clock):
    for _ in range(60):
        history.add(60)
        clock.advance(10)  # ten minutes of samples

    assert history.filled_fraction() > 0.9
