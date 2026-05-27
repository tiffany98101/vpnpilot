"""Main window: status panel, preset list, disconnect footer.

The main window is modeless. It reacts to controller.state_changed the
same way the tray does, but renders more detail. The tray remains the
sign-in path; this window only indicates auth status, it doesn't host
the sign-in flow.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from importlib.resources import files

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .browser import BrowseTab
from .catalog import ServerCatalog
from .controller import Controller
from .diagnostics import collect_diagnostics
from .paths import default_log_path
from .preset import (
    Preset,
    PresetStore,
    PresetTarget,
    TargetKind,
    decode_city_target,
)
from .preset_editor import PresetEditorDialog
from .setup_help import help_for_status
from .setup_help_dialog import SetupHelpDialog
from .state import AuthState, ConnectionInfo, ConnState

log = logging.getLogger(__name__)


_STATE_TEXT = {
    ConnState.CONNECTED: "Connected",
    ConnState.DISCONNECTED: "Disconnected",
    ConnState.TRANSITIONING: "Working…",
    ConnState.CLI_MISSING: "CLI not found",
    ConnState.CLI_ERROR: "CLI error",
    ConnState.NETWORK_OFFLINE: "Network offline",
    ConnState.EXTERNAL_VPN_ACTIVE: "External VPN active",
    ConnState.UNKNOWN: "Unknown",
}

# Color values match the tray icon palette closely enough; full
# theming is out of scope until a settings UI exists.
_STATE_STYLE = {
    ConnState.CONNECTED: "color: #1f8a36; font-size: 18pt; font-weight: 600;",
    ConnState.DISCONNECTED: "color: #888; font-size: 18pt; font-weight: 600;",
    ConnState.TRANSITIONING: "color: #c98a00; font-size: 18pt; font-weight: 600;",
    ConnState.CLI_MISSING: "color: #a33; font-size: 18pt; font-weight: 600;",
    ConnState.CLI_ERROR: "color: #a33; font-size: 18pt; font-weight: 600;",
    ConnState.NETWORK_OFFLINE: "color: #888; font-size: 18pt; font-weight: 600;",
    ConnState.EXTERNAL_VPN_ACTIVE: "color: #c98a00; font-size: 18pt; font-weight: 600;",
    ConnState.UNKNOWN: "color: #888; font-size: 18pt; font-weight: 600;",
}


class StatusPanel(QFrame):
    """Compact status row at the top of the main window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(60)

        self.state_label = QLabel("Disconnected")
        self.state_label.setObjectName("statusState")
        self.state_label.setStyleSheet(_STATE_STYLE[ConnState.DISCONNECTED])

        self.server_label = QLabel("")
        self.server_label.setObjectName("statusServer")
        self.server_label.setVisible(False)

        self.protocol_label = QLabel("")
        self.protocol_label.setObjectName("statusProtocol")
        self.protocol_label.setVisible(False)

        self.backend_label = QLabel("")
        self.backend_label.setObjectName("statusBackend")
        self.backend_label.setVisible(False)

        self.auth_label = QLabel("(Not signed in)")
        self.auth_label.setObjectName("statusAuth")
        self.auth_label.setStyleSheet("color: #c00; font-weight: 600;")
        self.auth_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.addWidget(self.state_label)
        top.addStretch(1)
        top.addWidget(self.auth_label)
        layout.addLayout(top)

        layout.addWidget(self.server_label)
        layout.addWidget(self.protocol_label)
        layout.addWidget(self.backend_label)

    def render(self, info: ConnectionInfo) -> None:
        self.state_label.setText(_STATE_TEXT.get(info.state, str(info.state)))
        self.state_label.setStyleSheet(_STATE_STYLE.get(info.state, ""))

        if info.state is ConnState.CONNECTED and info.server:
            bits = [b for b in (info.city, info.country) if b]
            where = ", ".join(bits)
            self.server_label.setText(f"{info.server} — {where}" if where else info.server)
            self.server_label.setVisible(True)
        else:
            self.server_label.setVisible(False)

        if info.protocol:
            self.protocol_label.setText(f"Protocol: {info.protocol}")
            self.protocol_label.setVisible(True)
        else:
            self.protocol_label.setVisible(False)

        backend_bits = []
        if info.backend:
            backend_bits.append(f"Backend: {info.backend}")
        if info.active_profile:
            backend_bits.append(f"Profile: {info.active_profile}")
        if info.default_route_device:
            backend_bits.append(f"Default route: {info.default_route_device}")
        if info.dns_summary:
            backend_bits.append(f"DNS: {info.dns_summary}")
        if backend_bits:
            self.backend_label.setText(" | ".join(backend_bits))
            self.backend_label.setVisible(True)
        else:
            self.backend_label.setVisible(False)

        self.auth_label.setVisible(info.auth is AuthState.SIGNED_OUT)


def target_summary(target: PresetTarget) -> str:
    """Human-readable one-line description of a preset target."""
    if target.kind is TargetKind.NONE:
        return "fastest available"
    if target.kind is TargetKind.COUNTRY:
        return f"country {target.value}"
    if target.kind is TargetKind.CITY:
        country_code, city = decode_city_target(target.value)
        if country_code:
            return f"city {city} ({country_code}, best effort)"
        return f"city {city} (best effort)"
    if target.kind is TargetKind.SERVER_ID:
        return f"server {target.value}"
    return ""


def _flag_summary(preset: Preset) -> str:
    parts = []
    if preset.flags.p2p:
        parts.append("P2P")
    if preset.flags.secure_core:
        parts.append("Secure Core")
    if preset.flags.tor:
        parts.append("Tor")
    if preset.flags.random:
        parts.append("random")
    return ", ".join(parts)


def preset_display(preset: Preset) -> str:
    """Single-line preset description for the list view."""
    star = "★ " if preset.is_default else "   "
    flags = _flag_summary(preset)
    suffix = f" [{flags}]" if flags else ""
    return f"{star}{preset.name} — {target_summary(preset.target)}{suffix}"


def preset_matches_connection(preset: Preset, info: ConnectionInfo) -> bool:
    """True iff the current connection looks like it was made *by* this preset.

    Only the unambiguous match cases count: exact server-ID match, or
    case-insensitive city match. Country and 'none' are intentionally
    not matched here — the CLI accepts both country codes and names,
    and 'none' is "fastest available" which could reasonably re-pick a
    different server.
    """
    if info.state is not ConnState.CONNECTED:
        return False
    t = preset.target
    if t.kind is TargetKind.SERVER_ID:
        return (info.server or "") == t.value
    if t.kind is TargetKind.CITY:
        _country_code, city = decode_city_target(t.value)
        return (info.city or "").casefold() == city.casefold()
    return False


class PresetListModel(QAbstractListModel):
    """Qt model backed by a PresetStore. Refresh after store mutations."""

    PresetRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, store: PresetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._presets: list[Preset] = list(store.list_all())

    def refresh(self) -> None:
        self.beginResetModel()
        self._presets = list(self._store.list_all())
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._presets)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._presets):
            return None
        preset = self._presets[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return preset_display(preset)
        if role == self.PresetRole:
            return preset
        return None

    def preset_at(self, row: int) -> Preset | None:
        if 0 <= row < len(self._presets):
            return self._presets[row]
        return None


class PresetListPanel(QFrame):
    """List of presets + Connect/Edit/Delete/New/Set-Default buttons.

    Action callbacks are injected so the panel doesn't depend on the
    Controller or any dialog. The parent (MainWindow) wires them up.
    """

    def __init__(
        self,
        store: PresetStore,
        *,
        on_connect: Callable[[Preset], None],
        on_edit: Callable[[Preset], None],
        on_new: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("presetArea")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._store = store
        self._on_connect = on_connect
        self._on_edit = on_edit
        self._on_new = on_new
        self._current_info = ConnectionInfo(state=ConnState.DISCONNECTED)

        self._model = PresetListModel(store, self)
        self.list_view = QListView()
        self.list_view.setObjectName("presetList")
        self.list_view.setModel(self._model)
        self.list_view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list_view.doubleClicked.connect(self._on_double_click)
        sel = self.list_view.selectionModel()
        sel.selectionChanged.connect(lambda *_: self._refresh_buttons())

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("presetConnect")
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.setObjectName("presetEdit")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("presetDelete")
        self.new_btn = QPushButton("New…")
        self.new_btn.setObjectName("presetNew")
        self.set_default_btn = QPushButton("Set Default")
        self.set_default_btn.setObjectName("presetSetDefault")

        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.new_btn.clicked.connect(self._on_new_clicked)
        self.set_default_btn.clicked.connect(self._on_set_default_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.list_view, stretch=1)
        btns = QHBoxLayout()
        for b in (
            self.connect_btn,
            self.edit_btn,
            self.delete_btn,
            self.new_btn,
            self.set_default_btn,
        ):
            btns.addWidget(b)
        btns.addStretch(1)
        layout.addLayout(btns)

        # Select the first row by default if any presets exist.
        if self._model.rowCount() > 0:
            self.list_view.setCurrentIndex(self._model.index(0, 0))
        self._refresh_buttons()

    # ----- public -----

    def refresh(self) -> None:
        """Reload from the store. Preserves selection by preset id if possible."""
        selected_id = None
        p = self._selected_preset()
        if p is not None:
            selected_id = p.id
        self._model.refresh()
        # Restore selection.
        if selected_id is not None:
            for row in range(self._model.rowCount()):
                if self._model.preset_at(row) and self._model.preset_at(row).id == selected_id:
                    self.list_view.setCurrentIndex(self._model.index(row, 0))
                    break
        elif self._model.rowCount() > 0:
            self.list_view.setCurrentIndex(self._model.index(0, 0))
        self._refresh_buttons()

    def update_for_connection(self, info: ConnectionInfo) -> None:
        self._current_info = info
        self._refresh_buttons()

    # ----- internals -----

    def _selected_preset(self) -> Preset | None:
        rows = self.list_view.selectionModel().selectedRows()
        if not rows:
            current = self.list_view.currentIndex()
            if current.isValid():
                return self._model.preset_at(current.row())
            return None
        return self._model.preset_at(rows[0].row())

    def _refresh_buttons(self) -> None:
        preset = self._selected_preset()
        has_selection = preset is not None
        signed_out = self._current_info.auth is AuthState.SIGNED_OUT
        already_at_target = bool(preset and preset_matches_connection(preset, self._current_info))

        self.connect_btn.setEnabled(has_selection and not signed_out and not already_at_target)
        self.edit_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection and not (preset and preset.is_default))
        self.set_default_btn.setEnabled(has_selection and not (preset and preset.is_default))
        # new_btn always enabled.

    def _on_double_click(self, index: QModelIndex) -> None:
        preset = self._model.preset_at(index.row())
        if preset and self.connect_btn.isEnabled():
            self._on_connect(preset)

    def _on_connect_clicked(self) -> None:
        preset = self._selected_preset()
        if preset:
            self._on_connect(preset)

    def _on_edit_clicked(self) -> None:
        preset = self._selected_preset()
        if preset:
            self._on_edit(preset)

    def _on_new_clicked(self) -> None:
        self._on_new()

    def _on_delete_clicked(self) -> None:
        preset = self._selected_preset()
        if preset is None or preset.is_default:
            return
        reply = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete preset “{preset.name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._store.delete(preset.id)
        except ValueError as e:
            QMessageBox.warning(self, "Could not delete", str(e))
            return
        self.refresh()

    def _on_set_default_clicked(self) -> None:
        preset = self._selected_preset()
        if preset is None or preset.is_default:
            return
        self._store.set_default(preset.id)
        self.refresh()


class MainWindow(QMainWindow):
    """Modeless main window. Status + preset list + disconnect footer.

    Hide-on-close keeps the window state across openings — the tray
    item just shows/raises this single instance instead of building a
    new one each time.
    """

    def __init__(
        self,
        controller: Controller,
        preset_store: PresetStore,
        catalog: ServerCatalog | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(700, 500)
        try:
            self.setWindowIcon(QIcon(str(files("vpnpilot.resources").joinpath("icon-app.svg"))))
        except Exception:  # noqa: BLE001
            log.debug("could not load app icon for main window", exc_info=True)

        self._controller = controller
        self._preset_store = preset_store
        self._catalog = catalog
        self._diagnostics_task: asyncio.Task | None = None
        self._setup_help_dialog: SetupHelpDialog | None = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.status_panel = StatusPanel()
        outer.addWidget(self.status_panel)

        self.preset_panel = PresetListPanel(
            preset_store,
            on_connect=self._on_preset_connect,
            on_edit=self._on_preset_edit,
            on_new=self._on_preset_new,
        )

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("mainTabs")
        self.tab_widget.addTab(self.preset_panel, "Presets")

        if catalog is not None:
            self.browse_tab = BrowseTab(catalog, controller, parent=self)
            self.tab_widget.addTab(self.browse_tab, "Browse")
            self.tab_widget.currentChanged.connect(self._on_tab_changed)
        else:
            self.browse_tab = None

        outer.addWidget(self.tab_widget, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.setup_help_btn = QPushButton("Troubleshooting…")
        self.setup_help_btn.setObjectName("setupHelpButton")
        self.setup_help_btn.clicked.connect(self._open_setup_help)
        footer.addWidget(self.setup_help_btn)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("disconnectButton")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        footer.addWidget(self.disconnect_btn)
        outer.addLayout(footer)

        self._controller.state_changed.connect(self._on_state_changed)
        self._on_state_changed(controller.current)

    def closeEvent(self, event):  # noqa: N802 — Qt naming
        # Hide instead of destroy so re-opening preserves state.
        event.ignore()
        self.hide()

    def _on_tab_changed(self, index: int) -> None:
        if self.browse_tab is not None and self.tab_widget.widget(index) is self.browse_tab:
            self.browse_tab.on_shown()

    def _on_state_changed(self, info: ConnectionInfo) -> None:
        self.status_panel.render(info)
        self.preset_panel.update_for_connection(info)
        # Disconnect stays enabled whenever we observe CONNECTED,
        # regardless of auth state — see CLAUDE.md "Auth axis".
        self.disconnect_btn.setEnabled(info.state is ConnState.CONNECTED)

    def _on_disconnect(self) -> None:
        self._controller.disconnect()

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
            parent=self,
        )
        self._setup_help_dialog.show()
        self._setup_help_dialog.raise_()
        self._setup_help_dialog.activateWindow()

    def _copy_diagnostic_info(self) -> None:
        if self._diagnostics_task is not None and not self._diagnostics_task.done():
            return
        self._diagnostics_task = asyncio.create_task(self._collect_and_copy_diagnostics())

    async def _collect_and_copy_diagnostics(self) -> None:
        try:
            text = await collect_diagnostics(last_error=self._controller.last_error)
            QApplication.clipboard().setText(text)
        except Exception as e:  # noqa: BLE001
            log.exception("could not copy diagnostic info")
            QMessageBox.warning(self, "Could not copy diagnostics", str(e))
        else:
            QMessageBox.information(
                self, "Diagnostics copied", "Diagnostic info copied to clipboard."
            )

    def _open_log(self) -> None:
        log_path = default_log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))
        except OSError as e:
            log.exception("could not open log")
            QMessageBox.warning(self, "Could not open log", str(e))
            return
        if not opened:
            QMessageBox.information(self, "VPNPilot log", f"Log file: {log_path}")

    def _on_preset_connect(self, preset: Preset) -> None:
        self._controller.connect_preset(preset.id)

    def _on_preset_edit(self, preset: Preset) -> None:
        taken = {p.name for p in self._preset_store.list_all() if p.id != preset.id}
        dlg = PresetEditorDialog(
            preset=preset, taken_names=taken, catalog=self._catalog, parent=self
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, target, flags = dlg.values()
        try:
            self._preset_store.update(preset.id, name=name, target=target, flags=flags)
        except ValueError as e:
            QMessageBox.warning(self, "Could not save preset", str(e))
            return
        self.preset_panel.refresh()

    def _on_preset_new(self) -> None:
        taken = {p.name for p in self._preset_store.list_all()}
        dlg = PresetEditorDialog(preset=None, taken_names=taken, catalog=self._catalog, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, target, flags = dlg.values()
        try:
            self._preset_store.add(name=name, target=target, flags=flags)
        except ValueError as e:
            QMessageBox.warning(self, "Could not create preset", str(e))
            return
        self.preset_panel.refresh()
