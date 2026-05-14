"""Main window: status panel, preset list (placeholder), disconnect footer.

The main window is modeless. It reacts to controller.state_changed the
same way the tray does, but renders more detail. The tray remains the
sign-in path; this window only indicates auth status, it doesn't host
the sign-in flow.

Preset list area is a placeholder in this slice — the wiring lands in
the next slice (see CLAUDE.md "Things not yet built").
"""

from __future__ import annotations

import logging
from importlib.resources import files

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .controller import Controller
from .preset import PresetStore
from .state import AuthState, ConnectionInfo, ConnState

log = logging.getLogger(__name__)


_STATE_TEXT = {
    ConnState.CONNECTED: "Connected",
    ConnState.DISCONNECTED: "Disconnected",
    ConnState.TRANSITIONING: "Working…",
}

# Color values match the tray icon palette closely enough; full
# theming is out of scope until a settings UI exists.
_STATE_STYLE = {
    ConnState.CONNECTED: "color: #1f8a36; font-size: 18pt; font-weight: 600;",
    ConnState.DISCONNECTED: "color: #888; font-size: 18pt; font-weight: 600;",
    ConnState.TRANSITIONING: "color: #c98a00; font-size: 18pt; font-weight: 600;",
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

    def render(self, info: ConnectionInfo) -> None:
        self.state_label.setText(_STATE_TEXT.get(info.state, str(info.state)))
        self.state_label.setStyleSheet(_STATE_STYLE.get(info.state, ""))

        if info.state is ConnState.CONNECTED and info.server:
            bits = [b for b in (info.city, info.country) if b]
            where = ", ".join(bits)
            self.server_label.setText(
                f"{info.server} — {where}" if where else info.server
            )
            self.server_label.setVisible(True)
        else:
            self.server_label.setVisible(False)

        if info.protocol:
            self.protocol_label.setText(f"Protocol: {info.protocol}")
            self.protocol_label.setVisible(True)
        else:
            self.protocol_label.setVisible(False)

        self.auth_label.setVisible(info.auth is AuthState.SIGNED_OUT)


class MainWindow(QMainWindow):
    """Modeless main window. Status + preset placeholder + footer.

    Hide-on-close keeps the window state across openings — the tray
    item just shows/raises this single instance instead of building a
    new one each time.
    """

    def __init__(
        self,
        controller: Controller,
        preset_store: PresetStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(600, 450)
        try:
            self.setWindowIcon(
                QIcon(str(files("vpnpilot.resources").joinpath("icon-app.svg")))
            )
        except Exception:  # noqa: BLE001
            log.debug("could not load app icon for main window", exc_info=True)

        self._controller = controller
        self._preset_store = preset_store

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.status_panel = StatusPanel()
        outer.addWidget(self.status_panel)

        # Preset list placeholder. Real list view lands in the next
        # slice; the surrounding frame and footer wire-up are already
        # in place so the swap is contained.
        self._preset_area = QFrame()
        self._preset_area.setObjectName("presetArea")
        self._preset_area.setFrameShape(QFrame.Shape.StyledPanel)
        preset_layout = QVBoxLayout(self._preset_area)
        placeholder = QLabel("Presets — list view lands in the next slice.")
        placeholder.setObjectName("presetPlaceholder")
        placeholder.setStyleSheet("color: #888;")
        preset_layout.addWidget(placeholder)
        preset_layout.addStretch(1)
        outer.addWidget(self._preset_area, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
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

    def _on_state_changed(self, info: ConnectionInfo) -> None:
        self.status_panel.render(info)
        # Disconnect stays enabled whenever we observe CONNECTED,
        # regardless of auth state — see CLAUDE.md "Auth axis".
        self.disconnect_btn.setEnabled(info.state is ConnState.CONNECTED)

    def _on_disconnect(self) -> None:
        self._controller.disconnect()
