"""System tray icon + menu."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from importlib.resources import files

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__
from .catalog import ServerCatalog
from .controller import POLL_INTERVAL_CHOICES, Controller
from .detect import detect_official_proton_gui_processes
from .diagnostics import collect_diagnostics
from .main_window import MainWindow
from .paths import default_log_path
from .preset import Preset, PresetStore
from .setup_help import help_for_status
from .setup_help_dialog import SetupHelpDialog
from .signin_panel import SignInPanel
from .state import AuthState, ConnectionInfo, ConnState
from .user_state import NullPersistence, Persistence

log = logging.getLogger(__name__)
_GUI_CONFLICT_POLL_MS = 15000
_WAKE_CHECK_INTERVAL_MS = 30000
_WAKE_REFRESH_THRESHOLD_SECONDS = 90.0


def _preset_menu_label(preset: Preset) -> str:
    return f"★ {preset.name}" if preset.is_default else preset.name


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
        catalog: ServerCatalog | None = None,
        official_gui_detector: Callable[[], list[int]] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._app = app
        self._controller = controller
        self._preset_store = preset_store
        self._persistence = persistence or NullPersistence()
        self._catalog = catalog
        self._official_gui_detector = official_gui_detector or detect_official_proton_gui_processes
        self._last_gui_conflict_pids: set[int] = set()
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._last_wake_check_monotonic = self._monotonic_clock()
        self._last_wake_check_wall = self._wall_clock()
        self._tray = QSystemTrayIcon()
        self._tray.setToolTip(f"{APP_NAME} {__version__}")
        self._signin_panel: SignInPanel | None = None
        self._main_window: MainWindow | None = None
        self._setup_help_dialog: SetupHelpDialog | None = None
        self._gui_conflict_timer = QTimer(self._tray)
        self._gui_conflict_timer.setInterval(_GUI_CONFLICT_POLL_MS)
        self._gui_conflict_timer.timeout.connect(self._check_official_gui_conflict)
        self._wake_timer = QTimer(self._tray)
        self._wake_timer.setInterval(_WAKE_CHECK_INTERVAL_MS)
        self._wake_timer.timeout.connect(self._check_wake_or_clock_jump)
        self._diagnostics_task: asyncio.Task | None = None
        self._build_menu()
        self._connect_signals()
        self._render(controller.current)
        self._gui_conflict_timer.start()
        self._wake_timer.start()

    def show(self) -> None:
        self._tray.show()
        self._check_official_gui_conflict()

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

        self._backend_action = QAction("")
        self._backend_action.setEnabled(False)
        self._backend_action.setVisible(False)
        self._menu.addAction(self._backend_action)

        self._menu.addSeparator()

        # Open main window — present in every auth/connection state.
        # Left-click on the tray icon does the same thing (wired below).
        self._open_window_action = QAction("Open VPNPilot…")
        self._open_window_action.triggered.connect(self._open_main_window)
        self._menu.addAction(self._open_window_action)

        self._refresh_now_action = QAction("Refresh now")
        self._refresh_now_action.triggered.connect(
            lambda checked=False: self._controller.force_refresh()
        )
        self._menu.addAction(self._refresh_now_action)

        self._refresh_interval_menu = QMenu("Refresh interval", self._menu)
        self._refresh_interval_group = QActionGroup(self._menu)
        self._refresh_interval_group.setExclusive(True)
        self._refresh_interval_actions: dict[str, QAction] = {}
        labels = {
            "manual": "Manual only",
            "2m": "Every 2 minutes",
            "5m": "Every 5 minutes",
            "10m": "Every 10 minutes",
            "30m": "Every 30 minutes",
        }
        for key in POLL_INTERVAL_CHOICES:
            action = QAction(labels[key], self._refresh_interval_menu)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked=False, selected=key: self._controller.set_poll_interval_key(selected)
            )
            self._refresh_interval_menu.addAction(action)
            self._refresh_interval_group.addAction(action)
            self._refresh_interval_actions[key] = action
        self._menu.addMenu(self._refresh_interval_menu)
        self._sync_refresh_interval_menu()

        self._menu.addSeparator()

        self._setup_help_action = QAction("Troubleshooting / Setup Help")
        self._setup_help_action.triggered.connect(self._open_setup_help)
        self._menu.addAction(self._setup_help_action)

        self._copy_diagnostics_action = QAction("Copy Diagnostic Info")
        self._copy_diagnostics_action.triggered.connect(self._copy_diagnostic_info)
        self._menu.addAction(self._copy_diagnostics_action)

        self._open_log_action = QAction("Open Log")
        self._open_log_action.triggered.connect(self._open_log)
        self._menu.addAction(self._open_log_action)

        self._menu.addSeparator()

        # Dynamic connect section. The actions for "Connect to <default>"
        # and the "Connect to…" submenu are inserted before
        # _disconnect_action and rebuilt on `aboutToShow` so they reflect
        # the current preset store state without explicit notifications.
        self._dynamic_actions: list[QAction] = []
        self._connect_submenu: QMenu | None = None

        self._disconnect_action = QAction("Disconnect")
        self._disconnect_action.triggered.connect(lambda: self._controller.disconnect())
        self._menu.addAction(self._disconnect_action)

        self._menu.addSeparator()

        # NB: must keep a Python reference; addAction() doesn't take
        # ownership of the QAction, and a local would be GC'd.
        self._quit_action = QAction("Quit")
        self._quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(self._quit_action)

        # Rebuild dynamic section right before the user sees the menu.
        # Also rebuild now so the initial state is correct.
        self._menu.aboutToShow.connect(self._rebuild_connect_section)
        self._menu.aboutToShow.connect(self._sync_refresh_interval_menu)
        self._rebuild_connect_section()

        self._tray.setContextMenu(self._menu)

    def _sync_refresh_interval_menu(self) -> None:
        key = getattr(self._controller, "poll_interval_key", "10m")
        if key not in self._refresh_interval_actions:
            key = "10m"
        self._refresh_interval_actions[key].setChecked(True)

    def _rebuild_connect_section(self) -> None:
        """Rebuild the (Connect to default + submenu) section in place."""
        # Drop old dynamic actions/submenu — Qt does not auto-destroy
        # them when removeAction is called, so we keep the lambdas alive
        # via Python references only until this point.
        for action in self._dynamic_actions:
            self._menu.removeAction(action)
        self._dynamic_actions.clear()
        if self._connect_submenu is not None:
            self._connect_submenu.deleteLater()
            self._connect_submenu = None

        presets = self._preset_store.list_all()
        if not presets:
            return

        default = presets[0]

        connect_default = QAction(f"Connect to {default.name}", self._menu)
        default_id = default.id
        connect_default.triggered.connect(
            lambda checked=False, pid=default_id: self._controller.connect_preset(pid)
        )
        self._menu.insertAction(self._disconnect_action, connect_default)
        self._dynamic_actions.append(connect_default)

        submenu = QMenu("Connect to…", self._menu)
        for p in presets:
            act = submenu.addAction(_preset_menu_label(p))
            pid = p.id
            act.triggered.connect(lambda checked=False, x=pid: self._controller.connect_preset(x))
        submenu_action = self._menu.insertMenu(self._disconnect_action, submenu)
        self._dynamic_actions.append(submenu_action)
        self._connect_submenu = submenu

        # Apply current-state enable/disable to the freshly-built actions.
        self._apply_state_to_dynamic_actions(self._controller.current)

    def _apply_state_to_dynamic_actions(self, info: ConnectionInfo) -> None:
        # Disabled when signed-out (can't connect) or when something is
        # already in flight / connected. Matches the previous hardcoded
        # Seattle entry's enable rule.
        if info.auth is AuthState.SIGNED_OUT:
            enabled = False
        else:
            enabled = info.state is ConnState.DISCONNECTED
        for a in self._dynamic_actions:
            a.setEnabled(enabled)

    def _connect_signals(self) -> None:
        self._controller.state_changed.connect(self._render)
        self._controller.error_occurred.connect(self._on_error)
        # Left-click on the tray icon opens the main window. The tray
        # previously had no .activated handler, so this is purely
        # additive — context menu (right-click) keeps working as before.
        self._tray.activated.connect(self._on_tray_activated)

    def _check_wake_or_clock_jump(self) -> None:
        now_monotonic = self._monotonic_clock()
        now_wall = self._wall_clock()
        monotonic_elapsed = max(0.0, now_monotonic - self._last_wake_check_monotonic)
        wall_elapsed = max(0.0, now_wall - self._last_wake_check_wall)
        self._last_wake_check_monotonic = now_monotonic
        self._last_wake_check_wall = now_wall

        if max(monotonic_elapsed, wall_elapsed) < _WAKE_REFRESH_THRESHOLD_SECONDS:
            return
        log.info(
            "wake or clock jump detected; forcing status refresh "
            "(monotonic_elapsed=%.1fs wall_elapsed=%.1fs)",
            monotonic_elapsed,
            wall_elapsed,
        )
        self._controller.force_refresh()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._open_main_window()

    def _open_main_window(self) -> None:
        # Singleton: a single instance is created lazily and reused.
        # Hidden on close (see MainWindow.closeEvent), shown/raised here.
        if self._main_window is None:
            self._main_window = MainWindow(
                self._controller,
                self._preset_store,
                catalog=self._catalog,
            )
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _on_quit(self) -> None:
        self._controller.stop()
        self._tray.hide()
        self._app.quit()

    def _on_error(self, msg: str) -> None:
        log.warning("controller error: %s", msg)
        self._show_warning(msg, duration_ms=5000)

    def _copy_diagnostic_info(self) -> None:
        if self._diagnostics_task is not None and not self._diagnostics_task.done():
            return
        self._copy_diagnostics_action.setEnabled(False)
        self._diagnostics_task = asyncio.create_task(self._collect_and_copy_diagnostics())

    async def _collect_and_copy_diagnostics(self) -> None:
        log.info("diagnostic collection requested")
        try:
            text = await collect_diagnostics(last_error=self._controller.last_error)
            self._app.clipboard().setText(text)
        except Exception as e:  # noqa: BLE001
            log.exception("could not copy diagnostic info")
            self._show_warning(f"Could not copy diagnostic info: {e}", duration_ms=5000)
        else:
            self._show_info("Diagnostic info copied to clipboard.", duration_ms=3000)
        finally:
            self._copy_diagnostics_action.setEnabled(True)

    def _open_log(self) -> None:
        log_path = default_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))
        except OSError as e:
            log.exception("could not open log")
            self._show_warning(f"Could not open log: {e}", duration_ms=5000)
            return
        if not opened:
            log.warning("desktop service refused to open log: %s", log_path)
            self._show_warning(f"Log file: {log_path}", duration_ms=7000)

    def _open_setup_help(self) -> None:
        item = help_for_status(
            self._controller.current,
            last_error=self._controller.last_error,
            has_presets=bool(self._preset_store.list_all()),
        )
        self._setup_help_dialog = SetupHelpDialog(
            item,
            on_copy_diagnostics=self._copy_diagnostic_info,
            on_open_log=self._open_log,
        )
        self._setup_help_dialog.show()
        self._setup_help_dialog.raise_()
        self._setup_help_dialog.activateWindow()

    def _check_official_gui_conflict(self) -> None:
        """Show a warning when the official Proton GUI is likely running."""
        try:
            pids = sorted(set(self._official_gui_detector()))
        except Exception:  # noqa: BLE001
            log.exception("official GUI conflict probe failed")
            return

        if not pids:
            self._last_gui_conflict_pids.clear()
            return
        pid_set = set(pids)
        if pid_set == self._last_gui_conflict_pids:
            return
        self._last_gui_conflict_pids = pid_set

        if len(pids) == 1:
            pid_blob = f"pid {pids[0]}"
        else:
            pid_blob = "pids " + ", ".join(str(pid) for pid in pids)
        msg = (
            f"Official Proton VPN GUI appears to be running ({pid_blob}). "
            "Proton docs warn that the GUI and CLI should not run at the same time."
        )
        self._show_warning(msg, duration_ms=9000)

    def _show_warning(self, message: str, *, duration_ms: int) -> None:
        self._tray.showMessage(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning, duration_ms)

    def _show_info(self, message: str, *, duration_ms: int) -> None:
        self._tray.showMessage(
            APP_NAME, message, QSystemTrayIcon.MessageIcon.Information, duration_ms
        )

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
            self._backend_action.setVisible(False)
            self._signin_action.setVisible(True)
            self._disconnect_action.setEnabled(info.state is ConnState.CONNECTED)
            self._apply_state_to_dynamic_actions(info)
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
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(True)
                tip = "vpnpilot — connected"
                if info.server:
                    tip += f" ({info.server})"
                self._tray.setToolTip(tip)
            case ConnState.TRANSITIONING:
                self._tray.setIcon(_icon("icon-transitioning.svg"))
                self._status_action.setText("Status: working…")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — working…")
            case ConnState.CLI_MISSING:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: ProtonVPN CLI not found")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — ProtonVPN CLI not found")
            case ConnState.CLI_ERROR:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: ProtonVPN CLI error")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — ProtonVPN CLI error")
            case ConnState.NETWORK_OFFLINE:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: network offline")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — network offline")
            case ConnState.EXTERNAL_VPN_ACTIVE:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: external VPN active")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — external VPN active")
            case ConnState.UNKNOWN:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: unknown")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — status unknown")
            case _:
                self._tray.setIcon(_icon("icon-disconnected.svg"))
                self._status_action.setText("Status: disconnected")
                self._server_action.setVisible(False)
                self._render_backend_line(info)
                self._disconnect_action.setEnabled(False)
                self._tray.setToolTip("vpnpilot — disconnected")
        self._apply_state_to_dynamic_actions(info)

    def _render_backend_line(self, info: ConnectionInfo) -> None:
        if not info.backend:
            self._backend_action.setVisible(False)
            return
        suffix = f" — {info.active_profile}" if info.active_profile else ""
        self._backend_action.setText(f"Backend: {info.backend}{suffix}")
        self._backend_action.setVisible(True)


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
