"""System tray icon + menu."""

from __future__ import annotations

import logging
from importlib.resources import files

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__
from .controller import Controller
from .main_window import MainWindow
from .preset import PresetStore
from .signin_panel import SignInPanel
from .state import AuthState, ConnectionInfo, ConnState
from .user_state import NullPersistence, Persistence

log = logging.getLogger(__name__)


def _icon(name: str) -> QIcon:
    path = files("vpnpilot.resources").joinpath(name)
    return QIcon(str(path))


class TrayApp:
    """Owns the tray icon and routes menu actions to the controller."""

    def __init__(
        self,
        app: QApplication,
        controller: Controller,
        *,
        preset_store: PresetStore,
        persistence: Persistence | None = None,
    ) -> None:
        self._app = app
        self._controller = controller
        self._preset_store = preset_store
        self._persistence = persistence or NullPersistence()
        self._tray = QSystemTrayIcon()
        self._tray.setToolTip(f"{APP_NAME} {__version__}")
        self._signin_panel: SignInPanel | None = None
        self._main_window: MainWindow | None = None
        self._build_menu()
        self._connect_signals()
        self._render(controller.current)

    def show(self) -> None:
        self._tray.show()

    # ----- menu -----

    def _build_menu(self) -> None:
        self._menu = QMenu()

        self._signin_action = QAction("Sign in…")
        self._signin_action.triggered.connect(self._open_signin_panel)
        self._signin_action.setVisible(False)
        self._menu.addAction(self._signin_action)

        self._status_action = QAction("Status: unknown")
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._server_action = QAction("")
        self._server_action.setEnabled(False)
        self._server_action.setVisible(False)
        self._menu.addAction(self._server_action)

        self._menu.addSeparator()

        # Open main window — present in every auth/connection state.
        # Left-click on the tray icon does the same thing (wired below).
        self._open_window_action = QAction("Open VPNPilot…")
        self._open_window_action.triggered.connect(self._open_main_window)
        self._menu.addAction(self._open_window_action)

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
        # Left-click on the tray icon opens the main window. The tray
        # previously had no .activated handler, so this is purely
        # additive — context menu (right-click) keeps working as before.
        self._tray.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._open_main_window()

    def _open_main_window(self) -> None:
        # Singleton: a single instance is created lazily and reused.
        # Hidden on close (see MainWindow.closeEvent), shown/raised here.
        if self._main_window is None:
            self._main_window = MainWindow(self._controller, self._preset_store)
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _on_quit(self) -> None:
        self._controller.stop()
        self._tray.hide()
        self._app.quit()

    def _on_error(self, msg: str) -> None:
        log.warning("controller error: %s", msg)
        self._tray.showMessage(APP_NAME, msg, QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ----- signed-in panel -----

    def _open_signin_panel(self) -> None:
        # Singleton: don't stack multiple panels.
        if self._signin_panel is not None and self._signin_panel.isVisible():
            self._signin_panel.raise_()
            self._signin_panel.activateWindow()
            return
        self._signin_panel = SignInPanel(
            last_email=self._persistence.last_email(),
            on_recheck=self._controller.force_refresh,
            state_signal=self._controller.state_changed,
        )
        self._signin_panel.show()
        self._signin_panel.raise_()
        self._signin_panel.activateWindow()

    # ----- rendering -----

    def _render(self, info: ConnectionInfo) -> None:
        # Auth state takes priority over connection state for the UI:
        # if we're signed out, the whole "connect/disconnect" UX is
        # unavailable and we surface the sign-in path instead.
        if info.auth is AuthState.SIGNED_OUT:
            self._tray.setIcon(_icon("icon-signed-out.svg"))
            self._status_action.setText("Status: not signed in")
            self._server_action.setVisible(False)
            self._signin_action.setVisible(True)
            self._connect_seattle_action.setEnabled(False)
            self._disconnect_action.setEnabled(False)
            self._tray.setToolTip("ProtonVPN: not signed in")
            return

        # auth is SIGNED_IN or UNKNOWN — render the connection axis as usual.
        self._signin_action.setVisible(False)
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
