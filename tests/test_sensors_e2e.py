"""End-to-end tests for every sensor source, reading real temperatures off the running machine.

Deselected by default via addopts; run them with `just test-e2e` or `uv run pytest -m e2e`.
A source that is not running is skipped, not failed.
"""

import asyncio

import pytest

from iets_speed_control.sensors import create_provider

pytestmark = pytest.mark.e2e

# Wide on purpose: this guards against parsing bugs (a mis-handled "63,0" arriving as 630.0),
# not against odd readings. Sources legitimately report 0.0 for configured sensor limits.
PLAUSIBLE_RANGE = (-50.0, 200.0)

# Sources file configured thresholds under the same Temperature type as live readings, and those
# sit at 0.0 by design -- LibreHardwareMonitor reports "Thermal Sensor Low Limit" as 0 for this
# machine's RAM. All markers below come from labels actually observed on real hardware.
THRESHOLD_MARKERS = ("limit", "warning temperature", "critical temperature", "resolution")


def is_threshold(label: str) -> bool:
    """True for entries that describe a configured limit rather than a measured temperature."""
    lowered = label.lower()
    return any(marker in lowered for marker in THRESHOLD_MARKERS)


def test_returns_real_temperatures(provider_name):
    temperatures = create_provider(provider_name).get_temperatures()

    assert temperatures, f"{provider_name} returned no temperatures"
    assert all(isinstance(label, str) and label for label in temperatures), "sensor labels must be non-empty strings"
    assert all(isinstance(value, float) for value in temperatures.values()), "temperatures must be floats"

    low, high = PLAUSIBLE_RANGE
    out_of_range = {label: value for label, value in temperatures.items() if not low <= value <= high}
    assert not out_of_range, f"implausible temperatures from {provider_name}: {out_of_range}"


def test_live_sensors_never_read_zero(provider_name):
    """A measured 0 C means the sensor stopped reporting.

    The control loop takes the maximum of the filtered temperatures, so zeros silently drag the
    fan down instead of raising an error -- exactly the failure this test exists to catch.
    """
    temperatures = create_provider(provider_name).get_temperatures()

    assert any(value != 0.0 for value in temperatures.values()), (
        f"{provider_name} reported 0 for every sensor -- the source is not measuring anything"
    )

    zeroed = {label: value for label, value in temperatures.items() if value == 0.0 and not is_threshold(label)}
    assert not zeroed, f"{provider_name} reported 0 C for live sensors: {sorted(zeroed)}"


def test_matches_independent_query(provider_name, raw_temperatures, probe):
    """Cross-check the provider against the source queried directly.

    Sources drop sensors at random: over 40 raw AIDA64 probes, two came back short and three
    different labels each vanished once. So neither side is trusted on a single reading -- both
    are sampled twice, and only a sensor the source reported *both* times is demanded of the
    provider, which in turn may report it in *either* of its two reads. A one-off flicker is
    absorbed; a sensor the provider systematically drops still fails.
    """
    provider = create_provider(provider_name)
    before = raw_temperatures
    first = provider.get_temperatures()
    second = provider.get_temperatures()
    after = probe()

    stable = set(before) & set(after)
    assert stable, "source reported no sensor consistently across both snapshots"

    seen = set(first) | set(second)
    assert stable <= seen, f"provider dropped sensors: {sorted(stable - seen)}"

    invented = seen - set(before) - set(after)
    assert not invented, f"provider reported sensors the source never did: {sorted(invented)}"

    # Readings move between queries, so compare loosely rather than for equality.
    drifted = {
        label: (first[label], before[label])
        for label in stable & set(first)
        if abs(first[label] - before[label]) > 25.0
    }
    assert not drifted, f"provider values differ wildly from the raw query: {drifted}"


def test_repeated_reads_stay_usable(provider_name):
    """COM is initialized once per thread, so a second read on the same thread must still work."""
    provider = create_provider(provider_name)

    first = provider.get_temperatures()
    second = provider.get_temperatures()

    assert first, "first read returned nothing"
    assert second, "second read returned nothing -- the source is only usable once"
    assert set(first) & set(second), "the two reads have no sensor in common"


async def test_readable_from_a_worker_thread(provider_name):
    """The control loop reads sensors via asyncio.to_thread, which needs COM set up per thread."""
    provider = create_provider(provider_name)

    from_worker = await asyncio.to_thread(provider.get_temperatures)

    assert from_worker, f"{provider_name} returned nothing when called off the event loop"
    assert set(from_worker) & set(provider.get_temperatures()), "worker thread saw no sensor the main thread saw"
