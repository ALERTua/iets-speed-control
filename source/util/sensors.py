import pythoncom
from wmi import WMI

from source.util.env import *


def get_sensors():
    # noinspection PyUnresolvedReferences
    pythoncom.CoInitialize()
    wmi_obj = WMI(namespace="root\\WMI")
    output = {}
    try:
        sensor_values = wmi_obj.AIDA64_SensorValues()
    except:
        logging.error("Error connecting to AIDA64")
        return output

    for v in sensor_values:
        output[v.wmi_property('Label').Value] = v.wmi_property('Value').Value
    return output


if __name__ == '__main__':
    wmi_obj_ = WMI(namespace="root\\WMI")
    cls = wmi_obj_.get('LENOVO_GAMEZONE_DATA')
    tmp = cls.GetCPUTemp()

    pass
