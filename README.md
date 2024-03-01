
Control the PWM of your laptop cooling pad fan, based on your Windows CPU/GPU temperatures, taken from AIDA64, using ESP32 via USB.

How it works:

- Gets temperatures from AIDA64
- Filters them by CPU and GPU sensors
- Takes the maximum int value among all temperatures
- Sends PWM command (`Dimmer {value}` by default) to the serial device

Preparation: 
- Run AIDA64
- In AIDA64 Preferences->External Applications->Enable writing sensors to WMI
- In AIDA64 Preferences->External Applications0>Enable Temperature sensors
- Keep AIDA64 open
- Connect your Serial Device
- Attach PWM pin to a fan (do not forget Ground)
- Create `.env` and fill it using `.env.example </.env.example>`_
- Run `run.cmd </run.cmd>`_
