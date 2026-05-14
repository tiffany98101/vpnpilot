"""Application bootstrap: Qt + qasync event loop + controller wiring."""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import subprocess
import sys
from importlib.resources import files

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME
from ._qasync_shim import QEventLoop
from ._singleton import SingletonLock
from .cli import ProtonCLI
from .controller import Controller
from .detect import default_detector
from .tray import TrayApp, ensure_tray_available
from .user_state import JsonStateStore

log = logging.getLogger(__name__)


def _app_icon() -> QIcon:
    return QIcon(str(files("vpnpilot.resources").joinpath("icon-app.svg")))


def _show_cli_missing(parent_app: QApplication) -> int:
    QMessageBox.critical(
        None,
        f"{APP_NAME} — CLI not detected",
        (
            "The official ProtonVPN CLI was not found in your PATH.\n\n"
            "Install it with:\n"
            "  sudo dnf install proton-vpn-cli\n\n"
            "Then sign in with:\n"
            "  protonvpn signin <email>\n\n"
            "Once installed, launch vpnpilot again."
        ),
    )
    return 1


def _notify_already_running(pid: int | None) -> None:
    msg = (
        f"vpnpilot is already running (pid {pid})."
        if pid
        else "vpnpilot is already running."
    )
    print(msg, file=sys.stderr)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", APP_NAME, msg], check=False)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Held for the life of this process — if released too early a second
    # launch could slip in. The local binding keeps the file descriptor
    # alive.
    lock = SingletonLock()
    if not lock.acquire():
        _notify_already_running(lock.held_by_pid())
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # tray app stays alive
    app.setWindowIcon(_app_icon())

    if not ensure_tray_available(app):
        return 1

    if not ProtonCLI.is_installed():
        return _show_cli_missing(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    cli = ProtonCLI()
    detector = default_detector(cli)
    store = JsonStateStore()
    controller = Controller(cli, detector, persistence=store)

    tray = TrayApp(app, controller, persistence=store)
    tray.show()

    # Ctrl-C in a terminal should exit cleanly.
    def _sigint(*_):
        log.info("SIGINT received, shutting down")
        controller.stop()
        app.quit()

    signal.signal(signal.SIGINT, _sigint)

    # Start polling once the loop is alive.
    loop.call_soon(controller.start)

    with loop:
        return loop.run_forever() or 0


if __name__ == "__main__":
    sys.exit(main())
