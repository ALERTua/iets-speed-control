import os
import logging

__LOGGER_SET_UP = False


def logger_setup():
    global __LOGGER_SET_UP
    if __LOGGER_SET_UP:
        return

    verbose = os.getenv("VERBOSE")
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger()
    console = logging.StreamHandler()
    logger.handlers = [console]
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)

    __LOGGER_SET_UP = True
