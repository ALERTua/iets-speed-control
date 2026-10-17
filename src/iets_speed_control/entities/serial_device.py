import asyncio
import json
import logging
import re

import aioserial
from serial.serialutil import SerialException

from ..util.config import CONFIG

logger = logging.getLogger(__name__)

# Seconds after which a read or a write is taken as one that will never finish. The port's own timeouts
# end a normal exchange well within a second, but after a resume from sleep the USB serial driver can leave
# a request pending forever, and the control loop would wait on it with it.
EXCHANGE_TIMEOUT = 5.0


def require_connection(func):
    async def wrapper(self, *args, **kwargs):
        if not self.connected:
            await self.connect()
        if not self.connected:
            logger.error("Connection not established.")
            return None

        return await func(self, *args, **kwargs)

    return wrapper


class SerialDevice:
    def __init__(self, port=None, baudrate=None, timeout=None):
        # None means "whatever the configuration says now". Defaulting to CONFIG.device.* in the
        # signature would freeze the values at import time, so a device rebuilt after the user
        # edited the port in the settings panel would still open the old one.
        self.port = CONFIG.device.port if port is None else port
        self.baudrate = CONFIG.device.baudrate if baudrate is None else baudrate
        self.timeout = CONFIG.device.timeout if timeout is None else timeout
        self.serial: aioserial.AioSerial | None = None
        # Whether the last attempt to open the port failed, so an outage is logged once rather than every tick.
        self._open_failing = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()

    @property
    def connected(self) -> bool:
        serial = self.serial
        if serial is None or not serial.is_open or serial.closed:
            return False

        # An unplugged adapter still reports itself open; touching it is what reveals the loss. Any
        # error counts, not only "Access is denied": a port that cannot be read cannot drive the fan.
        try:
            _ = serial.in_waiting
        except SerialException as e:
            logger.debug(f"{self.port} is open but not usable: {e}")
            return False

        return True

    async def connect(self):
        if self.connected:
            return True

        if self.serial is not None:
            self._drop_port("it stopped answering")

        try:
            self.serial = aioserial.AioSerial(
                port=self.port,
                baudrate=self.baudrate,
                write_timeout=self.timeout,
                timeout=self.timeout,
            )
        except (OSError, ValueError) as e:
            # SerialException subclasses OSError; ValueError covers bad port/baudrate settings. The loop tries
            # again every tick for as long as the adapter is away, so only the first failure is an error.
            if self._open_failing:
                logger.debug(f"Still unable to connect to {self.port}: {e}")
            else:
                logger.error(f"Error: Unable to connect to {self.port}. {e}")
                self._open_failing = True
            return False

        # Said at the level of the failure it ends, so a log that kept the error also keeps the recovery.
        logger.log(logging.WARNING if self._open_failing else logging.INFO, f"Connected to {self.port}")
        self._open_failing = False
        return True

    def _drop_port(self, reason: str):
        """Close a port that failed, so the next attempt opens a fresh handle instead of fighting this one.

        A handle the driver gave up on, after a resume from sleep or an unplug, still holds the port: opening
        the same port again while it is held fails with "Access is denied". Nothing else closes it, because
        `connected` already reads it as gone.
        """
        serial, self.serial = self.serial, None
        if serial is None:
            return

        logger.error(f"Lost {self.port}: {reason}. Closing it to reconnect.")
        try:
            serial.close()
        except Exception as e:  # noqa: BLE001 -- a dead handle can fail to close in any way; it is dropped regardless
            logger.debug(f"Closing the dead handle of {self.port} failed: {e!r}")

    async def disconnect(self):
        # Forgotten as well as closed: the next connect() must open a fresh handle, not report this one as lost.
        serial, self.serial = self.serial, None
        if serial is None:
            return

        try:
            serial.close()
        except Exception as e:  # noqa: BLE001 -- a handle that died while open can fail to close; it is gone either way
            logger.debug(f"Closing {self.port} failed: {e!r}")
        logger.info(f"Disconnected from {self.port}")

    @require_connection
    async def send_command(self, command):
        if self.serial:
            try:
                await asyncio.wait_for(self.serial.write_async((command + "\n").encode()), EXCHANGE_TIMEOUT)
                await asyncio.sleep(0.1)  # Wait for the command to be processed
            except TimeoutError:
                self._drop_port(f"a write did not finish in {EXCHANGE_TIMEOUT:g} s")
            except OSError as e:
                # A port that refuses one write refuses the next ones too; a fresh handle is the only cure.
                self._drop_port(f"sending failed: {e}")

    @require_connection
    async def _read_line(self):
        if self.serial:
            try:
                return (await asyncio.wait_for(self.serial.read_until_async(), EXCHANGE_TIMEOUT)).decode()
            except TimeoutError:
                self._drop_port(f"a read did not finish in {EXCHANGE_TIMEOUT:g} s")
            except OSError as e:
                self._drop_port(f"reading failed: {e}")
        return ""

    async def _read_results(self):
        lines = []
        while True:
            line = await self._read_line()
            if not line:
                break

            lines.append(line)
        results = [_.strip() for _ in lines if "RESULT" in _]
        results = [re.sub(".*RESULT = ", "", _) for _ in results]
        output = []
        for result in results:
            try:
                output.append(json.loads(result))
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Error parsing result {result!r}: {e}")
                continue

        return output

    def discard_input(self):
        """Throw away anything already waiting to be read.

        The device answers a set as well as a query, and those replies are never read at the time, so
        they queue up in the driver's buffer. Whatever is in there predates the query about to be
        sent, and returning it made this app read back values it had written itself seconds earlier --
        which the controller then reported as someone changing the dimmer from outside.
        """
        if not self.serial:
            return

        try:
            self.serial.reset_input_buffer()
        except OSError as e:
            logger.debug(f"Could not clear the input buffer: {e}")

    async def read_field_value(self, field_name) -> int | None:
        """Query one field and return its value, or None when the device does not answer."""
        self.discard_input()
        await self.send_command(field_name)

        # Last match rather than first: a device that echoes the command before answering it puts the
        # answer at the end.
        for result in reversed(await self._read_results()):
            if field_name in result:
                return result[field_name]

        return None

    async def set_field_value(self, field_name, value):
        """Set one field. The reply is left in the buffer and dropped by the next read.

        Reading it here would cost a read timeout on every single tick, and nothing needs it.
        """
        await self.send_command(f"{field_name} {value}")
