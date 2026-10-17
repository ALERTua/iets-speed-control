"""CLI entrypoint for IETS Speed Control."""

import asyncio
import logging

from ..controller import SpeedController
from ..util.config import CONFIG
from ..util.logger import configure_logging

logger = logging.getLogger(__name__)


async def _cli():
    """Run the CLI control loop with retry on disconnect."""
    controller = SpeedController()
    initial = True

    # The controller reports (connected, running, sensors_ok, loop_ok); the console only needs the first.
    def on_status(connected, *_rest):
        nonlocal initial
        if initial and connected:
            if controller.monitoring:
                logger.info(f"Starting with max {controller.max_temp} °C. Fan: {controller.current_speed}")
            else:
                # max_temp would read 0 °C, which says nothing: no temperature is being watched.
                logger.info(
                    f"No sensor is tracked, so the fan runs at {CONFIG.control.default_speed} %. "
                    "Track sensors under sensors.tracked in the configuration file or in the GUI's Settings."
                )
            initial = False
        elif not connected:
            logger.info(f"No device connected. Max: {controller.max_temp} °C")

    controller.set_callbacks(on_status_change=on_status)

    try:
        await controller.start()

        # Keep running until cancelled
        while True:
            await asyncio.sleep(1)

            # Retry logic: if disconnected for too long, wait and retry
            if not controller.connected:
                await asyncio.sleep(9)  # Additional wait (total 10s)

    except asyncio.CancelledError:
        logger.info(f"Setting fan to the default speed, {CONFIG.control.default_speed} %")
        await controller.shutdown()


def cli():
    """Main CLI entrypoint."""
    configure_logging()
    return asyncio.run(_cli())


if __name__ == "__main__":
    cli()
