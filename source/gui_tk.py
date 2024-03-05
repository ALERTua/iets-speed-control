import tkinter as tk
from tkinter import ttk
import asyncio
from typing import Optional, List
from source.util.sensors import get_sensors
from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from source.util.env import *  # import dotenv first
from source.entities.serial_device import SerialDevice
from source.util.tools import calculate_dimmer_value
from enum import Enum


class Step(Enum):
    INIT = 0
    CONNECTING = 1
    CONNECTED = 2


class App:
    async def exec(self):
        # noinspection PyAttributeOutsideInit
        self.window = Window()
        await asyncio.gather(
            self.window.connect(),
            self.window.read_sensors(),
            self.window.read_dimmer(),
            self.window.set_dimmer(),
            self.window.show(),
        )


class Window(tk.Tk):
    # noinspection PyMissingConstructor
    def __init__(self):
        super().__init__()
        self.title("IETS Speed Control")
        self.resizable(False, False)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # self.attributes('-toolwindow', True)
        self.style = ttk.Style()
        self.style.layout(
            'text.Horizontal.TProgressbar',
            [
                (
                    'Horizontal.Progressbar.trough',
                    {
                        'children': [
                            (
                                'Horizontal.Progressbar.pbar',
                                {'side': 'left', 'sticky': 'ns'}
                            )
                        ],
                        'sticky': 'nswe'
                    }
                ),
                (
                    'Horizontal.Progressbar.label',
                    {
                        'sticky': 'nswe'
                    }
                )
            ]
        )
        self.style.configure('text.Horizontal.TProgressbar', text='0', anchor='center')
        self.dimmer_var: tk.IntVar = tk.IntVar(value=0)

        self.CPU_label: Optional[tk.Label] = None
        self.CPU_value: Optional[tk.Label] = None
        self.GPU_label: Optional[tk.Label] = None
        self.GPU_value: Optional[tk.Label] = None
        self.step_label: Optional[tk.Label] = None
        self.progressbar: Optional[ttk.Progressbar] = None
        self.button_connect: Optional[tk.Button] = None

        self.cpu_temp_var: tk.IntVar = tk.IntVar(value=0)
        self.gpu_temp_var: tk.IntVar = tk.IntVar(value=0)
        self.serial_device = SerialDevice()

        self._step_var: tk.IntVar = tk.IntVar(value=Step.INIT.value)

        self.draw()

    def draw(self):
        row = 1
        column = 1
        self.CPU_label = tk.Label(text="CPU")
        self.CPU_label.grid(row=row, column=column)

        column += 1
        self.CPU_value = tk.Label(textvariable=self.cpu_temp_var)
        self.CPU_value.grid(row=row, column=column)

        column += 2
        self.GPU_label = tk.Label(text="GPU")
        self.GPU_label.grid(row=row, column=column)

        column += 1
        self.GPU_value = tk.Label(textvariable=self.gpu_temp_var)
        self.GPU_value.grid(row=row, column=column)

        row += 1
        column = 1
        self.progressbar = ttk.Progressbar(length=200, orient='horizontal', mode='determinate',
                                           style='text.Horizontal.TProgressbar', variable=self.dimmer_var)
        self.progressbar.grid(row=row, column=column, columnspan=99, padx=(8, 8), pady=(16, 8))

        row += 1
        column = 1

        self.step_label = tk.Label(text=Step.INIT.name)
        self.step_label.grid(row=row, column=column)


    @property
    def step(self):
        return self._step_var.get()

    @step.setter
    def step(self, value: Step):
        step_old = self._step_var.get()
        logging.debug(f"Step {step_old}->{value.name}")
        self._step_var.set(value.value)
        self.step_label['text'] = value.name
        # self.step_label['text'] = value.name

    async def connect(self):
        while True:
            # logging.debug("connect")
            if not self.serial_device.connected:
                logging.debug(f"connecting")
                self.step = Step.CONNECTING
                if self.serial_device.connect():
                    logging.debug(f"connected")
                    self.step = Step.CONNECTED
            if not self.serial_device.connected:
                logging.debug(f"connecting 2")
                self.step = Step.CONNECTING
                coms: List[ListPortInfo] = comports()
                coms_match = [_ for _ in coms if DEVICE_NAME in _.description]
                if coms_match:
                    com = coms_match[0]
                    self.serial_device.port = com.device
                    logging.info(f"Serial Device found at {self.serial_device.port}")
                    if self.serial_device.connect():
                        logging.debug(f"connected 2")
                        self.step = Step.CONNECTED.value
            await asyncio.sleep(DELAY)

    async def read_dimmer(self):
        while True:
            # logging.debug("read_dimmer")
            if self.step >= Step.CONNECTED.value:
                dimmer_value = self.serial_device.read_dimmer_value()
                self.dimmer_var.set(dimmer_value)
                # logging.debug(f"dimmer {type(dimmer_value)} {dimmer_value}")
                self.style.configure('text.Horizontal.TProgressbar', text=str(dimmer_value))
            else:
                logging.debug(f"read_dimmer pause: {self.step} < {Step.CONNECTED.value}")
            await asyncio.sleep(DELAY)

    async def read_sensors(self):
        while True:
            # logging.debug("read_sensors")
            sensors = get_sensors()

            cpu_temperatures = {k: int(v) for k, v in sensors.items() if 'CPU' in k}
            gpu_temperatures = {k: int(v) for k, v in sensors.items() if 'GPU' in k}

            self.cpu_temp = max(cpu_temperatures.values() or [0])
            self.gpu_temp = max(gpu_temperatures.values() or [0])
            await asyncio.sleep(1)

    @property
    def dimmer(self):
        return self.dimmer_var.get()

    @dimmer.setter
    def dimmer(self, value):
        old_dimmer = self.dimmer
        if old_dimmer != value:
            logging.info(f"CPU: {self.cpu_temp}, GPU: {self.gpu_temp}."
                         f" {PWM_COMMAND}: {old_dimmer} -> {value}")

            self.serial_device.set_dimmer_value(value)
        self.dimmer_var.set(value)

    @property
    def cpu_temp(self):
        return self.cpu_temp_var.get()

    @cpu_temp.setter
    def cpu_temp(self, value):
        if self.cpu_temp_var.get() != value:
            self.cpu_temp_var.set(value)

    @property
    def gpu_temp(self):
        return self.gpu_temp_var.get()

    @gpu_temp.setter
    def gpu_temp(self, value):
        if self.gpu_temp_var.get() != value:
            self.gpu_temp_var.set(value)

    async def set_dimmer(self):
        while True:
            # logging.debug("set_dimmer")
            cpu_dimmer = calculate_dimmer_value(self.cpu_temp)
            gpu_dimmer = calculate_dimmer_value(self.gpu_temp)

            new_value = max(cpu_dimmer, gpu_dimmer) or PWM_DEFAULT

            if self.dimmer != new_value:
                self.dimmer = new_value

            await asyncio.sleep(DELAY)

    async def show(self):
        logging.debug("show")
        while True:
            self.update()
            await asyncio.sleep(.1)


if __name__ == '__main__':
    asyncio.run(App().exec())
