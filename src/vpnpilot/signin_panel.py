"""Sign-in panel: a modeless dialog that explains how to sign in.

Closes itself automatically when the detection pipeline observes
auth == SIGNED_IN. The user runs `protonvpn signin <email>` in a
terminal of their choice — we don't embed sign-in (TOTP/password
handling is the CLI's job).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .state import AuthState, ConnectionInfo

EXPLANATION = (
    "ProtonVPN requires you to sign in via the command line. "
    "TOTP and other factors can't be handled inside this app. "
    "Run the command below in a terminal, then come back here."
)
EMAIL_PLACEHOLDER = "<email>"
RECHECK_INTERVAL_MS = 5000


def build_signin_command(last_email: str | None) -> str:
    return f"protonvpn signin {last_email or EMAIL_PLACEHOLDER}"


class SignInPanel(QDialog):
    """Modeless sign-in helper.

    Parameters:
      last_email     — pre-fills the suggested command if available.
      on_recheck     — called when the user clicks Recheck or the 5s
                       auto-poll fires. Implementations should schedule
                       a detection pass; the panel itself doesn't know
                       about asyncio.
      state_signal   — a pyqtSignal(object) emitting ConnectionInfo.
                       Panel subscribes and closes on auth=SIGNED_IN.
    """

    def __init__(
        self,
        *,
        last_email: str | None,
        on_recheck: Callable[[], None],
        state_signal,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_recheck = on_recheck
        self._state_signal = state_signal
        self.setWindowTitle("Sign in to ProtonVPN")
        self.setModal(False)
        self.resize(420, 230)

        self._build_ui(build_signin_command(last_email))

        state_signal.connect(self._on_state_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(RECHECK_INTERVAL_MS)
        self._timer.timeout.connect(self._fire_recheck)
        self._timer.start()

    # ----- UI construction -----

    def _build_ui(self, command_text: str) -> None:
        layout = QVBoxLayout(self)

        explanation = QLabel(EXPLANATION)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        row = QHBoxLayout()
        self._cmd_field = QLineEdit(command_text)
        self._cmd_field.setReadOnly(True)
        self._cmd_field.setCursorPosition(0)
        row.addWidget(self._cmd_field, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("copyButton")
        copy_btn.clicked.connect(self._on_copy)
        row.addWidget(copy_btn)
        layout.addLayout(row)

        layout.addStretch(1)

        self._status_label = QLabel("Auto-rechecking every 5 seconds…")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        recheck_btn = QPushButton("Recheck now")
        recheck_btn.setObjectName("recheckButton")
        recheck_btn.clicked.connect(self._fire_recheck)
        buttons.addWidget(recheck_btn)
        layout.addLayout(buttons)

    # ----- public for tests -----

    def command_text(self) -> str:
        return self._cmd_field.text()

    # ----- internals -----

    def _on_copy(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.command_text())

    def _fire_recheck(self) -> None:
        # The callback owns its own error reporting; we just stay open.
        with contextlib.suppress(Exception):
            self._on_recheck()

    def _on_state_changed(self, info: ConnectionInfo) -> None:
        if info.auth is AuthState.SIGNED_IN:
            self._timer.stop()
            self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        # Ensure the timer is stopped and the signal is disconnected so
        # the panel can be safely garbage-collected.
        self._timer.stop()
        with contextlib.suppress(TypeError, RuntimeError):
            self._state_signal.disconnect(self._on_state_changed)
        super().closeEvent(event)
