import logging
import pythoncom

from wmi import WMI


def get_sensors():
    # noinspection PyUnresolvedReferences
    pythoncom.CoInitialize()
    wmi_obj = WMI(namespace="root\\WMI")
    output = {}
    try:
        sensor_values = wmi_obj.AIDA64_SensorValues()
    except Exception as e:
        logging.error(f"Error connecting to AIDA64: {str(e)}")
        return output

    for v in sensor_values:
        output[v.wmi_property("Label").Value] = v.wmi_property("Value").Value
    return output


def __main():
    a = get_sensors()
    _output = {}
    wmi_obj_ = WMI(namespace="root\\WMI")
    for wmi_class in sorted(wmi_obj_.classes.list):
        try:
            wmi_class_objs = wmi_obj_.instances(wmi_class)
        except Exception:
            continue

        for wmi_class_obj in wmi_class_objs:
            wmi_dict_obj = _output.setdefault(wmi_class, {})
            wmi_dict_obj_name = wmi_class_obj.id
            ole_object = wmi_class_obj.ole_object
            try:
                ole_object_name = ole_object.Name
            except:
                try:
                    ole_object_name = ole_object.Label
                except:
                    try:
                        ole_object_name = ole_object.ID
                    except:
                        try:
                            ole_object_name = ole_object.InstanceName
                        except:
                            ole_object_name = wmi_dict_obj_name

            wmi_dict_obj[ole_object_name or wmi_dict_obj_name] = (
                wmi_class_obj.ole_object
            )
    cls = wmi_obj_.get("AIDA64_SensorValues")
    tmp = cls.GetCPUTemp()

    pass


if __name__ == "__main__":
    __main()
