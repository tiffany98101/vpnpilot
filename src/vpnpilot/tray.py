"""System tray icon + menu."""

from __future__ import annotations

import logging
from importlib.resources import files

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__
from .controller import Controller
from .state import ConnectionInfo, ConnState

log = logging.getLogger(__name__)


def _icon(name: str) -> QIcon:
    path = files("vpnpilot.resources").joinpath(name)
    return QIcon(str(path))


class TrayApp:
    """Owns the tray icon and routes menu actions to the controller."""

    def __init__(self, app: QApplication, controller: Controller) -> None:
        self._app = app
        self._controller = controller
        self._tray = QSystemTrayIcon()
        self._tray.setToolTip(f"{APP_NAME} {__version__}")
        self._build_menu()
        self._connect_signals()
        self._render(controller.current)

    def show(self) -> None:
        self._tray.show()

    # ----- menu -----

    def _build_menu(self) -> None:
        self._menu = QMenu()

        self._status_action = QAction("Status: unknown")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._server_action = QAction("")
        self._server_action.setEnabled(False)
        self._server_action.setVisible(False)
        self._menu.addAction(self._server_action)

        self._menu.addSeparator()

        self._connect_seattle_action = QAction("Connect to Seattle")
        self._connect_seattle_action.triggered.connect(
            lambda: self._controller.connect_preset_seattle()
        )
        self._menu.addAction(self._connect_seattle_action)

        self._disconnect_action = QAction("Disconnect")
        self._disconnect_action.triggered.connect(lambda: self._controller.disconnect())
        self._menu.addAction(self._disconnect_action)

        self._menu.addSeparator()

        quit_action = QAction("Quit")
        quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(quit_action)

        self._tray.setContextMenu(self._menu)

    def _connect_signals(self) -> None:
        self._controller.state_changed.connect(self._render)
        self._controller.error_occurred.connect(self._on_error)

    def _on_quit(self) -> None:
        self._controller.stop()
        self._tray.hide()
        self._app.quit()

    def _on_error(self, msg: str) -> None:
        log.warning("controller error: %s", msg)
        self._tray.showMessage(APP_NAME, msg, QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ----- rendering -----

    def _render(self, info: ConnectionInfo) -> None:
        match info.state:
            case ConnState.CONNECTED:
                self._tray.setIcon(_icon("icon-connected.svg"))
                self._status_action.setText("Status: connected")
                if info.server:
                    bits = [b for b in (info.city, info.country) if b]
                    where = ", ".join(bits) if bits else info.server
                    self._server_action.setText(f"{info.server} — {where}")
                    self._server_action.setVisible(True)
                else:
                    self._server_action.setVisible(False)
                self._connect_seattle_action.setEnabled(False)
                self._disconnect_action.setEnabled(True)
                tip = "vpnpilot — connected"
                if info.server:
                    tip += f" ({info.server})"
                self._tray.setToolTip(tip)
            case ConnState.TRANSITIONING:
                self._tray.setIcon(_icon("icon-transitioning.svg"))
                self._status_action.setText("Status: working…")
                self._server_action.setVisible(False)
                self._connect_seattle_action.setEnabled(False)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — working…")
            case _:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: disconnected")
                self._server_action.setVisible(False)
                self._connect_seattle_action.setEnabled(True)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — disconnected")


def ensure_tray_available(parent_app: QApplication) -> bool:
    """Return True if a tray is usable. Otherwise, show a guidance dialog."""
    if QSystemTrayIcon.isSystemTrayAvailable():
        return True
    desktop = _detect_desktop_env()
    if desktop == "gnome":
        msg = (
            "No system tray detected.\n\n"
            "GNOME does not ship a tray by default. Install the "
            "AppIndicator and KStatusNotifierItem Support extension:\n\n"
            "  https://extensions.gnome.org/extension/615/appindicator-support/\n\n"
            "Then log out and back in (or restart GNOME Shell) and launch vpnpilot again."
        )
    else:
        msg = (
            "No system tray detected on this desktop.\n\n"
            "vpnpilot requires a system tray indicator (StatusNotifierItem / AppIndicator). "
            "Please enable a tray-supporting panel or extension and try again."
        )
    QMessageBox.critical(None, "vpnpilot — tray unavailable", msg)
    return False


def _detect_desktop_env() -> str:
    import os

    desktops = (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").lower()
    if "gnome" in desktops:
        return "gnome"
    if "kde" in desktops or "plasma" in desktops:
        return "kde"
    return "unknown"
