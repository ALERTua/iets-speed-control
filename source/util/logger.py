import os
import logging
from colorlog import ColoredFormatter

__LOGGER_SET_UP = False


def logger_setup():
    global __LOGGER_SET_UP
    if __LOGGER_SET_UP:
        return

    formatter = ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(message)s",
        datefmt=None,
        reset=True,
        log_colors={
            'DEBUG': 'grey',
            'INFO': 'white',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red',
        }
    )

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    if os.getenv('VERBOSE'):
        console.setLevel(logging.DEBUG)
    logger.handlers = [console]

    __LOGGER_SET_UP = True
