### Using Tasmota on ESP32

- Get for example a ESP32C3 from AliExpress
- Connect it via USB
- Open Device Manager
- Check unknown devices. If the ESP32 is not recognized - install drivers using, for example, Zadig
- Install [Tasmota](https://tasmota.github.io/install/) (Edge/Chrome only)
- Connect the device to your Wi-Fi to get access to the web ui
- Enter the web ui and set up the PWM pin
- Connect the PWM pin on the device to the PWM pin on the fan
- Connect the Ground pin on the device to the Ground pin on the fan
- The device is now connectable via Serial, the PWM is controllable using `Dimmer 0` to `Dimmer 100` command
