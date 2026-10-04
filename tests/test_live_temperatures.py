"""Sources hand over only what a chip measures right now.

LibreHardwareMonitor files limits, a sensor's resolution and Distance to TjMax under the same
Temperature type as real readings. The fan follows the hottest selected reading, so a limit of 85 °C
or a margin that grows as the chip cools would drive it from a number that is not a temperature.
"""

import pytest

from iets_speed_control.sensors import LibreHardwareMonitorWebProvider
from iets_speed_control.sensors.base import is_live_temperature

# Labels as LibreHardwareMonitor reports them on an i9-13900HX / RTX 4090 laptop.
LIVE = [
    "CPU Package",
    "Core Max",
    "P-Core #1",
    "E-Core #16",
    "GPU Hot Spot",
    "GPU Memory Junction",
    "DIMM #0",
    "Composite Temperature",
    "Temperature #2",
]
NOT_LIVE = [
    "P-Core #1 Distance to TjMax",
    "Temperature Sensor Resolution",
    "Thermal Sensor Low Limit",
    "Thermal Sensor Critical High Limit",
    "Warning Temperature",
    "Critical Temperature",
]


@pytest.mark.parametrize("label", LIVE)
def test_a_reading_of_the_chip_is_kept(label):
    assert is_live_temperature(label)


@pytest.mark.parametrize("label", NOT_LIVE)
def test_a_limit_or_margin_is_dropped(label):
    assert not is_live_temperature(label)


def test_the_web_source_drops_what_is_not_a_reading():
    document = {
        "Text": "Sensor",
        "Children": [
            {
                "Text": "PC",
                "Children": [
                    {
                        "Text": "Samsung SSD 990 PRO 1TB",
                        "Children": [
                            {
                                "Text": "Temperatures",
                                "Children": [
                                    {"Text": "Composite Temperature", "Type": "Temperature", "Value": "51,0 °C"},
                                    {"Text": "Critical Temperature", "Type": "Temperature", "Value": "84,0 °C"},
                                ],
                            }
                        ],
                    },
                    {
                        "Text": "13th Gen Intel Core i9-13900HX",
                        "Children": [
                            {
                                "Text": "Temperatures",
                                "Children": [
                                    {"Text": "Core Max", "Type": "Temperature", "Value": "87,0 °C"},
                                    {"Text": "P-Core #1 Distance to TjMax", "Type": "Temperature", "Value": "17,0 °C"},
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
    }
    found: dict[str, float] = {}

    LibreHardwareMonitorWebProvider()._collect(document, [], found)

    assert found == {
        "Samsung SSD 990 PRO 1TB/Composite Temperature": 51.0,
        "13th Gen Intel Core i9-13900HX/Core Max": 87.0,
    }


class FakeSensor:
    def __init__(self, name, value, identifier):
        self.Name = name
        self.Value = value
        self.Identifier = identifier


def test_the_wmi_source_drops_what_is_not_a_reading(monkeypatch):
    from iets_speed_control.sensors import lhm

    sensors = [
        FakeSensor("Core Max", 87.0, "/intelcpu/0/temperature/0"),
        FakeSensor("P-Core #1 Distance to TjMax", 17.0, "/intelcpu/0/temperature/27"),
        FakeSensor("Thermal Sensor High Limit", 55.0, "/memory/dimm/0/temperature/3"),
    ]

    class FakeWmi:
        def __init__(self, namespace):
            pass

        def Sensor(self, SensorType):
            return sensors

    monkeypatch.setattr(lhm, "WMI", FakeWmi)
    monkeypatch.setattr(lhm, "ensure_com_initialized", lambda: None)

    assert lhm.LibreHardwareMonitorProvider().get_temperatures() == {"Core Max": 87.0}
