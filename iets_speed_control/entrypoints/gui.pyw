import logging
import wx
import asyncio

from enum import Enum
from typing import List, Optional
from pathlib import Path
from wxasync import AsyncBind, WxAsyncApp, StartCoroutine

from wx.adv import TaskBarIcon as TaskBarIcon
from wx.lib.agw.pygauge import PyGauge
from serial.tools.list_ports_common import ListPortInfo
from serial.tools.list_ports_windows import comports

from ..entities.dimmer import Dimmer
from ..util import env
from ..util.tools import calculate_dimmer_value

from ..util.sensors import get_sensors

APP_NAME = "iets-speed-control"


class Step(Enum):
    INIT = 0
    CONNECTING = 1
    CONNECTED = 2


class SpeedControlTaskBarIcon(TaskBarIcon):
    def __init__(self, frame):
        TaskBarIcon.__init__(self)

        self.frame = frame

        icon_path = Path(__file__).parent.parent.parent / "media" / "icon.png"
        icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_PNG)
        self.SetIcon(icon, "Task bar icon")

        # ------------

        AsyncBind(wx.EVT_MENU, self.on_task_bar_activate, self.frame, id=1)
        AsyncBind(wx.EVT_MENU, self.on_task_bar_deactivate, self.frame, id=2)
        AsyncBind(wx.EVT_MENU, self.on_task_bar_close, self.frame, id=3)

    # -----------------------------------------------------------------------

    def CreatePopupMenu(self):
        menu = wx.Menu()
        menu.Append(1, "Show")
        menu.Append(2, "Hide")
        menu.Append(3, "Close")
        return menu

    async def on_task_bar_close(self, event):
        self.frame.Close()

    async def on_task_bar_activate(self, event):
        if not self.frame.IsShown():
            self.frame.Show()

    async def on_task_bar_deactivate(self, event):
        if self.frame.IsShown():
            self.frame.Hide()


class SpeedControlFrame(wx.Frame):
    def __init__(self, parent=None):
        super(SpeedControlFrame, self).__init__(
            parent, style=wx.DEFAULT_FRAME_STYLE ^ wx.MAXIMIZE_BOX
        )
        self.SetWindowStyle(style=self.GetWindowStyle() ^ wx.RESIZE_BORDER)

        icon_path = Path(__file__).parent.parent.parent / "media/icon.png"
        if icon_path.exists():
            icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_ANY, -1, -1)
            self.SetIcon(icon)

        self.tskic = SpeedControlTaskBarIcon(self)
        AsyncBind(wx.EVT_CLOSE, self.on_close, self)
        AsyncBind(wx.EVT_ICONIZE, self.draw, self)

        self.step_label = None
        self.port_label = None
        self.progressbar: Optional[PyGauge] = None
        self.gpu_value = None
        self.gpu_label = None
        self.cpu_value = None
        self.cpu_label = None

        self.serial_device = Dimmer()
        self._step = Step.INIT
        self._dimmer = 0
        self._cpu_temp = 0
        self._gpu_temp = 0

        StartCoroutine(self.draw(), self)
        self.SetMinSize(wx.Size(200, -1))

        self.load_window_position()
        self.start()

    def load_window_position(self):
        # noinspection PyUnresolvedReferences
        config = wx.Config(APP_NAME)
        x = config.ReadInt("WindowPosX", -1)
        y = config.ReadInt("WindowPosY", -1)

        # Get the desktop size
        desktop = wx.Display().GetClientArea()
        desktop_width, desktop_height = desktop.GetWidth(), desktop.GetHeight()

        # Check if the saved position is within the desktop area
        if x != -1 and y != -1 and 0 <= x < desktop_width and 0 <= y < desktop_height:
            self.SetPosition(wx.Point(x, y))

    def save_window_position(self):
        pos = self.GetPosition()
        # noinspection PyUnresolvedReferences
        config = wx.Config(APP_NAME)
        config.WriteInt("WindowPosX", pos.x)
        config.WriteInt("WindowPosY", pos.y)
        config.Flush()

    async def draw(self, *args, **kwargs):
        self.sizer()
        self.GetSizer().Fit(self)
        self.Layout()

    async def on_close(self, event):
        logging.debug("OnClose")
        self.save_window_position()
        self.dimmer = 0
        self.tskic.Destroy()
        self.Destroy()
        event.Skip()  # Allow the window to close normally

    def start(self):
        logging.debug("start")
        StartCoroutine(self.loop_connect, self)

    def sizer(self):
        logging.debug("generating sizer")
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        center_sizer = wx.BoxSizer(wx.VERTICAL)
        sizer = wx.GridBagSizer(5, 5)
        sizer.SetEmptyCellSize(wx.Size(10, 10))

        row = 0
        column = 0

        column += 1

        self.cpu_label = wx.StaticText(
            self, label="CPU", style=wx.ALIGN_CENTRE_HORIZONTAL
        )
        sizer.Add(self.cpu_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 1

        self.cpu_value = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.cpu_value, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 3

        self.gpu_label = wx.StaticText(
            self, label="GPU", style=wx.ALIGN_CENTRE_HORIZONTAL
        )
        sizer.Add(self.gpu_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        column += 1

        self.gpu_value = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.gpu_value, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        row += 1
        column = 0

        self.progressbar = PyGauge(
            self, range=100, size=(150, 18), style=wx.GA_HORIZONTAL
        )
        self.progressbar.SetDrawValue(
            draw=True, drawPercent=True, font=None, colour=wx.BLACK, formatString=None
        )
        # self.progressbar.SetBackgroundColour(wx.BLACK)
        # self.progressbar.SetBorderColor(wx.BLACK)
        self.progressbar.SetBarColour(wx.GREEN)
        sizer.Add(
            self.progressbar,
            pos=(row, column),
            span=(1, sizer.GetCols()),
            flag=wx.ALIGN_CENTRE,
        )

        row += 1
        column = 0
        self.step_label = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(self.step_label, pos=(row, column), span=(1, 1), flag=wx.ALIGN_CENTRE)

        self.port_label = wx.StaticText(self, style=wx.ALIGN_CENTRE_HORIZONTAL)
        sizer.Add(
            self.port_label,
            pos=(row, sizer.GetCols() - 2),
            span=(1, 1),
            flag=wx.ALIGN_CENTRE,
        )

        sizer.Fit(self)
        center_sizer.Add(sizer, 1, wx.ALIGN_CENTER | wx.ALL, 0)
        main_sizer.Add(center_sizer, 1, wx.EXPAND | wx.ALL, 0)
        self.SetSizer(main_sizer)

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

    @property
    def progress(self) -> int | None:
        if self.progressbar:
            return self.progressbar.GetValue()

        return None

    @progress.setter
    def progress(self, value):
        if not value:
            return

        new_value = value
        current_value = self.progress
        if current_value is not None:
            new_value = value - current_value
        # logging.debug(f"Progressbar {current_value}->{_value}")
        if self.progressbar and new_value:
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
            logging.info(
                f"CPU: {self.cpu_temp}, GPU: {self.gpu_temp}."
                f" {env.PWM_COMMAND}: {old_dimmer} -> {value}"
            )

            StartCoroutine(self.serial_device.set_dimmer_value(value), self)
            # self.serial_device.set_dimmer_value(value)
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
        return self._gpu_temp

    @gpu_temp.setter
    def gpu_temp(self, value):
        if self._gpu_temp != value:
            self._gpu_temp = value
            if self.gpu_value:
                self.gpu_value.SetLabel(str(value))

    async def connect(self):
        logging.debug("connecting")
        self.step = Step.CONNECTING

        coro = StartCoroutine(self.serial_device.connect(), self)
        coro.add_done_callback(self.on_connect)

    def on_connect(self, event=None):
        if self.serial_device.connected:
            logging.debug("connected")
            self.step = Step.CONNECTED

    async def loop_connect(self):
        try:
            while True:
                # logging.debug("connect")
                if self.serial_device.connected and self.step != Step.CONNECTED:
                    self.step = Step.CONNECTED
                elif self.step == Step.CONNECTED and not self.serial_device.connected:
                    logging.debug("Disconnected. Reconnecting")
                    await self.connect()
                elif self.step == Step.CONNECTED:
                    sensors = get_sensors()
                    cpu_temperatures = {
                        k: int(v)
                        for k, v in sensors.items()
                        if env.CPU_SENSOR_FILTER in k
                    }
                    gpu_temperatures = {
                        k: int(v)
                        for k, v in sensors.items()
                        if env.GPU_SENSOR_FILTER == k
                    }

                    self.cpu_temp = cpu_temp = max(cpu_temperatures.values() or [0])
                    self.gpu_temp = gpu_temp = max(gpu_temperatures.values() or [0])

                    dimmer = await self.serial_device.read_dimmer_value()
                    self.progress = self.dimmer = dimmer

                    cpu_dimmer = calculate_dimmer_value(self.cpu_temp, env.TEMP_RANGES)
                    gpu_dimmer = calculate_dimmer_value(self.gpu_temp, env.TEMP_RANGES)
                    new_value = max(cpu_dimmer, gpu_dimmer)

                    if dimmer is not None and env.MAX_STEP:
                        # if new_value > dimmer + MAX_STEP:  # limit up step
                        #     new_value = dimmer + MAX_STEP
                        if new_value < dimmer - env.MAX_STEP:  # limit down step
                            new_value = dimmer - env.MAX_STEP

                    if (
                        dimmer is not None
                        and abs(dimmer - new_value) < env.IGNORE_LESS_THAN
                    ):
                        # logging.info(f"Skipping too low: {dimmer} -> {new_value}")
                        pass
                    elif dimmer != new_value:
                        logging.info(
                            f"CPU: {cpu_temp}, GPU: {gpu_temp}. {env.PWM_COMMAND}: {dimmer} -> {new_value}"
                        )
                        self.dimmer = new_value
                elif self.step == Step.INIT:
                    await self.connect()
                elif self.step == Step.CONNECTING:
                    logging.debug("connecting 2")
                    self.step = Step.CONNECTING
                    coms: List[ListPortInfo] = comports()
                    coms_match = [_ for _ in coms if env.DEVICE_NAME in _.description]
                    if coms_match:
                        com = coms_match[0]
                        self.serial_device.port = com.device
                        logging.info(
                            f"Serial Device found at {self.serial_device.port}"
                        )
                        await self.connect()

                await asyncio.sleep(env.DELAY)
        except asyncio.CancelledError:
            self.dimmer = 0


async def _gui():
    app = WxAsyncApp()
    frame = SpeedControlFrame()
    frame.Show()
    app.SetTopWindow(frame)
    await app.MainLoop()


def gui():
    asyncio.run(_gui())


if __name__ == "__main__":
    gui()
