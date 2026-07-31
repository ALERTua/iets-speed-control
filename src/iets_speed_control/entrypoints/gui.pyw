"""GUI entrypoint. Everything of substance lives in the iets_speed_control.gui package."""

from ..gui import GUIApp  # type: ignore[unresolved-import]
from ..util.logger import configure_logging  # type: ignore[unresolved-import]


def gui():
    """Main entrypoint."""
    configure_logging()
    GUIApp().run()


if __name__ == "__main__":
    gui()
