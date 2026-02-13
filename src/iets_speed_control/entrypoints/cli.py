"""CLI entrypoint for IETS Speed Control."""

import asyncio
import logging

from ..controller import SpeedController


async def _cli():
    """Run the CLI control loop with retry on disconnect."""
    controller = SpeedController()
    initial = True

    def on_status(connected, running):
        nonlocal initial
        if initial and connected:
            logging.info(
                f"Starting with CPU: {controller.cpu_temp}, GPU: {controller.gpu_temp}. Fan: {controller.current_speed}"
            )
            initial = False
        elif not connected:
            logging.info(f"No device connected. CPU: {controller.cpu_temp}, GPU: {controller.gpu_temp}")

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
        logging.info("Setting fan to 0")
        await controller.shutdown()


def cli():
    """Main CLI entrypoint."""
    return asyncio.run(_cli())


if __name__ == "__main__":
    cli()
