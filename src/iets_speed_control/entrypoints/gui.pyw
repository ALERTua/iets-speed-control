"""GUI entrypoint. Everything of substance lives in the iets_speed_control.gui package."""

from ..gui import GUIApp  # ty: ignore[unresolved-import] -- a .pyw file is not a module to ty
from ..util.logger import configure_logging  # ty: ignore[unresolved-import]


def gui():
    """Main entrypoint."""
    configure_logging()
    GUIApp().run()


if __name__ == "__main__":
    gui()
