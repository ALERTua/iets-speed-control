"""The Lenovo Legion source, against a stand-in for the WMI class.

The real class answers only to an elevated process and only on a Legion laptop, so the e2e tests skip
it on an ordinary test run. These tests pin the behaviour that does not need the hardware: what an
unelevated process gets, which labels come back, and how a missing sensor is handled.
"""

import logging
from typing import ClassVar

import pytest

from iets_speed_control.sensors import (
    PROVIDERS,
    LenovoWmiProvider,
    create_provider,
    find_provider,
    lacks_admin_rights,
    lenovo_wmi,
)
from iets_speed_control.sensors import base as sensors_base

READINGS = {0x05040000: 83, 0x05050000: 72, 0x05010000: 74}


class FakeMethod:
    """LENOVO_OTHER_METHOD as the wmi package returns it: GetFeatureValue answers with a one-item tuple."""

    def __init__(self, readings):
        self.readings = readings

    def GetFeatureValue(self, IDs):
        value = self.readings[IDs]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):  # a firmware that answers with something other than one value
            return value
        return (value,)


class FakeWmi:
    instances: ClassVar[list] = []
    calls = 0

    def __init__(self, namespace):
        assert namespace == "root\\WMI"

    def LENOVO_OTHER_METHOD(self):
        FakeWmi.calls += 1
        return FakeWmi.instances


@pytest.fixture
def elevated(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: True)


@pytest.fixture
def not_elevated(monkeypatch):
    monkeypatch.setattr(sensors_base, "is_elevated", lambda: False)


@pytest.fixture
def fake_wmi(monkeypatch):
    FakeWmi.instances = [FakeMethod(dict(READINGS))]
    FakeWmi.calls = 0
    monkeypatch.setattr(lenovo_wmi, "WMI", FakeWmi)
    monkeypatch.setattr(lenovo_wmi, "ensure_com_initialized", lambda: None)
    return FakeWmi


def test_reads_cpu_gpu_and_chipset(elevated, fake_wmi):
    assert LenovoWmiProvider().get_temperatures() == {"CPU": 83.0, "GPU": 72.0, "PCH": 74.0}


def test_the_default_filters_find_one_cpu_and_one_gpu(elevated, fake_wmi):
    """The labels are bare, so the default filters "CPU" and "GPU" work without any tuning."""
    from iets_speed_control.util.filters import DEFAULT_FILTERS, select

    selection = select(LenovoWmiProvider().get_temperatures(), DEFAULT_FILTERS)

    assert [(match.pattern, match.label) for match in selection.matches] == [("CPU", "CPU"), ("GPU", "GPU")]


def test_without_admin_rights_it_says_so_and_does_not_query(not_elevated, fake_wmi, caplog):
    with caplog.at_level(logging.ERROR):
        assert LenovoWmiProvider().get_temperatures() == {}

    assert fake_wmi.calls == 0, "a query Windows will refuse is not worth making"
    assert any("administrator" in record.message for record in caplog.records)


def test_the_missing_rights_are_reported_once_not_on_every_poll(not_elevated, fake_wmi, caplog):
    """The loop reads about once a second; a line per read would bury the rest of the log."""
    provider = LenovoWmiProvider()

    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            provider.get_temperatures()

    assert len([r for r in caplog.records if "administrator" in r.message]) == 1


def test_an_answer_with_the_wrong_number_of_values_is_skipped(elevated, fake_wmi, caplog):
    fake_wmi.instances = [FakeMethod({**READINGS, 0x05050000: (72, 1)})]

    # Named, because the package logger keeps whatever level an earlier test configured.
    with caplog.at_level(logging.DEBUG, logger=lenovo_wmi.__name__):
        readings = LenovoWmiProvider().get_temperatures()

    assert readings == {"CPU": 83.0, "PCH": 74.0}
    assert any("expected one value" in record.message for record in caplog.records), (
        "a firmware that answers differently must not read as a model without the sensor"
    )


def test_a_sensor_the_model_lacks_does_not_hide_the_others(elevated, fake_wmi):
    # The first sensor read fails, so giving up at the first failure would lose both of the others.
    fake_wmi.instances = [FakeMethod({**READINGS, 0x05040000: OSError("not supported")})]

    assert LenovoWmiProvider().get_temperatures() == {"GPU": 72.0, "PCH": 74.0}


def test_a_zero_reading_is_dropped(elevated, fake_wmi):
    """A 0 °C entry would show up as a matching sensor in the filter check while measuring nothing."""
    fake_wmi.instances = [FakeMethod({**READINGS, 0x05050000: 0})]

    assert "GPU" not in LenovoWmiProvider().get_temperatures()


def test_a_machine_without_the_class_reads_as_unavailable(elevated, monkeypatch, caplog):
    def no_class(namespace):
        raise OSError("Invalid class")

    monkeypatch.setattr(lenovo_wmi, "WMI", no_class)
    monkeypatch.setattr(lenovo_wmi, "ensure_com_initialized", lambda: None)

    with caplog.at_level(logging.ERROR):
        assert LenovoWmiProvider().get_temperatures() == {}

    assert any("Lenovo Legion" in record.message for record in caplog.records)


def test_no_instance_reads_as_unavailable(elevated, fake_wmi):
    fake_wmi.instances = []

    assert LenovoWmiProvider().get_temperatures() == {}


def test_it_is_registered_under_its_code_name_and_aliases():
    assert PROVIDERS["lenovo-wmi"] is LenovoWmiProvider
    assert isinstance(create_provider("lenovo"), LenovoWmiProvider)
    assert find_provider(" Lenovo_WMI ") is LenovoWmiProvider
    assert find_provider("nonsense") is None


def test_only_this_source_asks_for_admin_rights(not_elevated):
    assert lacks_admin_rights(LenovoWmiProvider)
    assert lacks_admin_rights(LenovoWmiProvider())
    others = [provider for name, provider in PROVIDERS.items() if name != "lenovo-wmi"]
    assert not any(lacks_admin_rights(provider) for provider in others)
    assert not lacks_admin_rights(None)


def test_an_elevated_process_lacks_nothing(elevated):
    assert not lacks_admin_rights(LenovoWmiProvider)
