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
from .backend import build_backend
from .catalog import ServerCatalog
from .cli import ProtonCLI, missing_cli_message
from .controller import Controller
from .detect import default_detector
from .logging_setup import configure_logging
from .networkmanager import NetworkManagerOpenVPN, active_vpn, parse_nmcli_connections
from .preset import PresetStore
from .settings import (
    BACKEND_AUTO,
    BACKEND_NETWORKMANAGER_OPENVPN,
    BACKEND_PROTON_CLI,
    SettingsStore,
)
from .tray import TrayApp, ensure_tray_available
from .user_state import JsonStateStore

log = logging.getLogger(__name__)

STARTUP_POLL_DELAY_SECONDS = 30.0


def _app_icon() -> QIcon:
    return QIcon(str(files("vpnpilot.resources").joinpath("icon-app.svg")))


def _show_cli_missing(parent_app: QApplication) -> int:
    QMessageBox.critical(
        None,
        f"{APP_NAME} — CLI not detected",
        missing_cli_message() + "\n\nOnce installed, launch VPNPilot again.",
    )
    return 1


def _needs_proton_cli_at_startup(settings) -> bool:
    if settings.backend == BACKEND_PROTON_CLI:
        return True
    if settings.backend == BACKEND_NETWORKMANAGER_OPENVPN:
        return False
    if settings.backend != BACKEND_AUTO:
        return True
    if settings.networkmanager_profile:
        return False
    return not _active_nm_vpn_exists()


def _active_nm_vpn_exists() -> bool:
    if not NetworkManagerOpenVPN.is_installed():
        return False
    try:
        cp = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "NAME,UUID,TYPE,DEVICE",
                "connection",
                "show",
                "--active",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if cp.returncode != 0:
        return False
    return active_vpn(parse_nmcli_connections(cp.stdout, active=True)) is not None


def _notify_already_running(pid: int | None) -> None:
    msg = f"vpnpilot is already running (pid {pid})." if pid else "vpnpilot is already running."
    print(msg, file=sys.stderr)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", APP_NAME, msg], check=False)


def main() -> int:
    configure_logging()
    log.info("app startup")

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
        log.info("system tray unavailable; exiting without restart-worthy failure")
        return 0

    settings = SettingsStore().load()
    if _needs_proton_cli_at_startup(settings) and not ProtonCLI.is_installed():
        log.warning("Proton VPN CLI missing at startup")
        return _show_cli_missing(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    cli = ProtonCLI()
    detector = default_detector(cli)
    backend = build_backend(cli, detector, settings)
    store = JsonStateStore()
    preset_store = PresetStore()
    preset_store.load()
    controller = Controller(
        cli,
        detector,
        backend=backend,
        persistence=store,
        preset_store=preset_store,
    )
    catalog = ServerCatalog(cli)

    tray = TrayApp(app, controller, preset_store=preset_store, persistence=store, catalog=catalog)
    tray.show()

    # Ctrl-C in a terminal should exit cleanly.
    def _sigint(*_):
        log.info("SIGINT received, shutting down")
        controller.stop()
        app.quit()

    signal.signal(signal.SIGINT, _sigint)

    # Let the tray render before probing the Proton CLI. Catalog/city loading
    # stays lazy and must be triggered by the browser or preset editor.
    loop.call_later(STARTUP_POLL_DELAY_SECONDS, controller.start)

    with loop:
        return loop.run_forever() or 0


if __name__ == "__main__":
    sys.exit(main())
