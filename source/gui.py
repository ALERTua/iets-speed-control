from enum import Enum
from typing import List, Optional
from pathlib import Path
import wx
from wxasync import AsyncBind, WxAsyncApp, StartCoroutine
from wx.adv import TaskBarIcon as TaskBarIcon
from wx.lib.agw.pygauge import PyGauge
import asyncio

from source.util.env import *  # import dotenv first
from source.entities.serial_device import SerialDevice
from source.util.tools import calculate_dimmer_value

from source.util.sensors import get_sensors
from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports


class Step(Enum):
    INIT = 0
    CONNECTING = 1
    CONNECTED = 2


class SpeedControlTaskBarIcon(TaskBarIcon):
    def __init__(self, frame):
        TaskBarIcon.__init__(self)

        self.frame = frame

        self.SetIcon(wx.Icon('../media/icon.png', wx.BITMAP_TYPE_PNG), 'Task bar icon')

        # ------------

        AsyncBind(wx.EVT_MENU, self.OnTaskBarActivate, self.frame, id=1)
        AsyncBind(wx.EVT_MENU, self.OnTaskBarDeactivate, self.frame, id=2)
        AsyncBind(wx.EVT_MENU, self.OnTaskBarClose, self.frame, id=3)

    # -----------------------------------------------------------------------

    def CreatePopupMenu(self):
        menu = wx.Menu()
        menu.Append(1, 'Show')
        menu.Append(2, 'Hide')
        menu.Append(3, 'Close')
        return menu

    async def OnTaskBarClose(self, event):
        self.frame.Close()

    async def OnTaskBarActivate(self, event):
        if not self.frame.IsShown():
            self.frame.Show()

    async def OnTaskBarDeactivate(self, event):
        if self.frame.IsShown():
            self.frame.Hide()


class SpeedControlFrame(wx.Frame):
    def __init__(self, parent=None):
        super(SpeedControlFrame, self).__init__(parent)
        self.SetWindowStyle(style=self.GetWindowStyle() ^ wx.RESIZE_BORDER ^ wx.MAXIMIZE_BOX)

        icon_path = Path(__file__).parent.parent / 'media/icon.png'
        if icon_path.exists():
            icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_ANY, -1, -1)
            self.SetIcon(icon)

        self.tskic = SpeedControlTaskBarIcon(self)
        AsyncBind(wx.EVT_CLOSE, self.OnClose, self)
        AsyncBind(wx.EVT_ICONIZE, self.draw, self)

        self.step_label = None
        self.port_label = None
        self.progressbar: Optional[PyGauge] = None
        self.gpu_value = None
        self.gpu_label = None
        self.cpu_value = None
        self.cpu_label = None

        self.serial_device = SerialDevice()
        self._step = Step.INIT
        self._dimmer = 0
        self._cpu_temp = 0
        self._gpu_temp = 0

        StartCoroutine(self.draw(), self)
        self.SetMaxSize(self.Size)
        self.Centre()

        self.start()

    async def draw(self, *args, **kwargs):
        self.sizer()
        self.GetSizer().Fit(self)
        self.Layout()

    async def OnClose(self, event):
        logging.debug("OnClose")
        self.tskic.Destroy()
        self.Destroy()

    def start(self):
        logging.debug("start")
        StartCoroutine(self.loop_connect, self)
        StartCoroutine(self.loop_read_sensors_start, self)
        StartCoroutine(self.loop_read_dimmer, self)
        StartCoroutine(self.loop_update_progressbar, self)
        StartCoroutine(self.loop_set_dimmer, self)

    def sizer(self):
        sizer = wx.GridBagSizer(5, 5)
        sizer.SetEmptyCellSize((10, 10))

        row = 0
        column = 0

        column += 1

        self.cpu_label = wx.StaticText(self, label='CPU', style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.cpu_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 1

        self.cpu_value = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.cpu_value, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 3

        self.gpu_label = wx.StaticText(self, label='GPU', style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.gpu_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 1

        self.gpu_value = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.gpu_value, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        row += 1
        column = 0

        self.progressbar = PyGauge(self, range=100, size=(150, 18), style=wx.GA_HORIZONTAL)
        self.progressbar.SetDrawValue(draw=True, drawPercent=True, font=None, colour=wx.BLACK, formatString=None)
        # self.progressbar.SetBackgroundColour(wx.BLACK)
        # self.progressbar.SetBorderColor(wx.BLACK)
        self.progressbar.SetBarColour(wx.GREEN)
        sizer.Add(self.progressbar, pos=(row, column), span=(1, sizer.GetCols()), flag=wx.ALIGN_CENTRE)

        row += 1
        column = 0
        self.step_label = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.step_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        self.port_label = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.port_label, pos=(row, sizer.GetCols() - 2), span=(1, 1), flag=wx.ALIGN_CENTRE)

        sizer.Fit(self)
        self.SetSizer(sizer)

    @property
    def step(self):
        return self._step

    @step.setter
    def step(self, value: Step):
        step_old = self._step
        if step_old == value:
            return

        logging.debug(f"Step {step_old}->{value.name}")
        self._step = value
        if self.step_label:
            self.step_label.SetLabel(value.name)
        if value == Step.CONNECTED:
            if self.progressbar and not self.progressbar.Shown:
                self.progressbar.Show()
            if self.port_label:
                self.port_label.SetLabel(self.serial_device.port)
        elif value == Step.CONNECTING:
            if self.progressbar and self.progressbar.Shown:
                self.progressbar.Hide()

    async def _dimmer_set(self, value):
        self.serial_device.set_dimmer_value(value)

    async def loop_set_dimmer(self):
        while True:
            # logging.debug("set_dimmer")
            cpu_dimmer = calculate_dimmer_value(self.cpu_temp)
            gpu_dimmer = calculate_dimmer_value(self.gpu_temp)

            new_value = max(cpu_dimmer, gpu_dimmer) or PWM_DEFAULT

            if self.dimmer != new_value:
                self.dimmer = new_value

            await asyncio.sleep(DELAY)

    async def loop_read_dimmer(self):
        while True:
            # logging.debug("read_dimmer")
            if self.step.value >= Step.CONNECTED.value:
                self.dimmer = self.serial_device.read_dimmer_value()
                # logging.debug(f"dimmer {type(dimmer_value)} {dimmer_value}")
            else:
                logging.debug(f"read_dimmer pause: {self.step} < {Step.CONNECTED.value}")
            await asyncio.sleep(DELAY)

    async def loop_update_progressbar(self):
        while True:
            if self.step.value >= Step.CONNECTED.value:
                self.progress = self.dimmer
            else:
                logging.debug(f"update_progressbar pause: {self.step} < {Step.CONNECTED.value}")
            await asyncio.sleep(1)

    @property
    def progress(self) -> int:
        if self.progressbar:
            return self.progressbar.GetValue()

    @progress.setter
    def progress(self, value):
        if value is None:
            return

        new_value = value
        current_value = self.progress
        if current_value is not None:
            new_value = value - current_value
        # logging.debug(f"Progressbar {current_value}->{_value}")
        if self.progressbar:
            self.progressbar.Update(new_value, 100)

    @property
    def dimmer(self):
        return self._dimmer

    @dimmer.setter
    def dimmer(self, value):
        if value is None:
            return

        old_dimmer = self.dimmer
        if old_dimmer != value:
            logging.info(f"CPU: {self.cpu_temp}, GPU: {self.gpu_temp}."
                         f" {PWM_COMMAND}: {old_dimmer} -> {value}")

            StartCoroutine(self._dimmer_set(value), self)
        self._dimmer = value
        self.progress = value

    @property
    def cpu_temp(self):
        return self._cpu_temp

    @cpu_temp.setter
    def cpu_temp(self, value):
        if self._cpu_temp != value:
            self._cpu_temp = value
            if self.cpu_value:
                self.cpu_value.SetLabel(str(value))

    @property
    def gpu_temp(self):
        return self._cpu_temp

    @gpu_temp.setter
    def gpu_temp(self, value):
        if self._gpu_temp != value:
            self._gpu_temp = value
            if self.gpu_value:
                self.gpu_value.SetLabel(str(value))

    async def _coro_connect(self):
        self.serial_device.connect()

    async def connect(self):
        logging.debug("connecting")
        self.step = Step.CONNECTING

        coro = StartCoroutine(self._coro_connect(), self)
        coro.add_done_callback(self.OnConnect)

    def OnConnect(self, event=None):
        if self.serial_device.connected:
            logging.debug("connected")
            self.step = Step.CONNECTED
        # else:
        #     logging.debug("OnConnect received, but no connection. Retrying")
        #     StartCoroutine(self.connect(), self)

    async def loop_connect(self):
        while True:
            # logging.debug("connect")
            if self.serial_device.connected:
                self.step = Step.CONNECTED
            elif self.step == Step.INIT:
                await self.connect()
            elif self.step == Step.CONNECTING:
                logging.debug(f"connecting 2")
                self.step = Step.CONNECTING
                coms: List[ListPortInfo] = comports()
                coms_match = [_ for _ in coms if DEVICE_NAME in _.description]
                if coms_match:
                    com = coms_match[0]
                    self.serial_device.port = com.device
                    logging.info(f"Serial Device found at {self.serial_device.port}")
                    await self.connect()
            elif self.step == Step.CONNECTED:
                if not self.serial_device.connected:
                    logging.debug("Disconnected. Reconnecting")
                    await self.connect()

            await asyncio.sleep(DELAY * 3)

    async def _coro_get_sensors(self):
        return get_sensors()

    def draw_sensors(self, task: asyncio.Task):
        sensors = task.result()
        cpu_temperatures = {k: int(v) for k, v in sensors.items() if 'CPU' in k}
        gpu_temperatures = {k: int(v) for k, v in sensors.items() if 'GPU' in k}

        self.cpu_temp = max(cpu_temperatures.values() or [0])
        self.gpu_temp = max(gpu_temperatures.values() or [0])

        StartCoroutine(self.loop_read_sensors_start(), self)

    async def loop_read_sensors_start(self):
        await asyncio.sleep(DELAY)
        # logging.debug("read_sensors")
        coro = StartCoroutine(self._coro_get_sensors(), self)
        coro.add_done_callback(self.draw_sensors)


async def main():
    app = WxAsyncApp()
    frame = SpeedControlFrame()
    frame.Show()
    app.SetTopWindow(frame)
    await app.MainLoop()


if __name__ == '__main__':
    asyncio.run(main())
