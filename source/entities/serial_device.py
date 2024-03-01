import json
import logging
import re
import time
from typing import Optional

import serial

from source.util.env import *


def require_connection(func):
    def wrapper(self, *args, **kwargs):
        if not self.connected:
            self.connect()
        if not self.connected:
            logging.error(f"Connection not established.")
            return None

        return func(self, *args, **kwargs)

    return wrapper


class SerialDevice:
    def __init__(self, port=DEFAULT_PORT, baudrate=DEFAULT_BAUDRATE, dimmer_command=DIMMER_COMMAND, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dimmer_command = dimmer_command
        self.serial_conn: Optional[serial.Serial] = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disconnect()

    @property
    def connected(self):
        return self.serial_conn and self.serial_conn.is_open

    def reset(self):
        self.serial_conn = None

    def connect(self):
        if not self.connected:
            try:
                self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
                logging.info(f"Connected to {self.port}")
            except serial.SerialException as e:
                logging.error(f"Error: Unable to connect to {self.port}. {e}")
                self.reset()

    def disconnect(self):
        try:
            self.serial_conn.close()
            logging.info(f"Disconnected from {self.port}")
        except:
            pass

        self.reset()

    @require_connection
    def send_command(self, command):
        try:
            self.serial_conn.write(command.encode() + b'\n')
            time.sleep(0.1)  # Wait for the command to be processed
        except serial.SerialException as e:
            logging.error(f"Error sending command: {e}")
            self.reset()

    @require_connection
    def _read_line(self):
        try:
            return self.serial_conn.readline().decode().strip()
        except serial.SerialException as e:
            logging.error(f"Error reading line: {e}")
            self.reset()
            return ''

    def _read_results(self):
        lines = []
        while True:
            line = self._read_line()
            if not line:
                break

            lines.append(line)
        results = [_ for _ in lines if 'RESULT' in _]
        results = [re.sub('.*RESULT = ', '', _) for _ in results]
        output = []
        for result in results:
            try:
                output.append(json.loads(result))
            except:
                continue
        return output

    def read_command_result(self):
        results = self._read_results()
        if not results:
            return

        output = results[0]
        return output

    def read_field_value(self, field_name) -> Optional[int]:
        self.send_command(field_name)
        result = self.read_command_result()
        output = (result or {}).get(field_name, None)
        return output

    def set_field_value(self, field_name, value):
        self.send_command(f"{field_name} {value}")

    def read_dimmer_value(self) -> Optional[int]:
        return self.read_field_value(self.dimmer_command)

    def set_dimmer_value(self, value):
        return self.set_field_value(self.dimmer_command, value)
