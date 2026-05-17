"""Qt dialog for setup and troubleshooting help."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .setup_help import HelpItem


class SetupHelpDialog(QDialog):
    def __init__(
        self,
        item: HelpItem,
        *,
        on_copy_diagnostics: Callable[[], None],
        on_open_log: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("VPNPilot Troubleshooting")
        self.resize(520, 340)

        title = QLabel(item.title)
        title.setObjectName("setupHelpTitle")
        title.setStyleSheet("font-size: 16pt; font-weight: 600;")

        message = QLabel(item.message)
        message.setWordWrap(True)

        actions = QTextEdit()
        actions.setObjectName("setupHelpActions")
        actions.setReadOnly(True)
        actions.setMinimumHeight(100)
        actions.setPlainText(_format_body(item))

        copy_btn = QPushButton("Copy Diagnostic Info")
        copy_btn.clicked.connect(on_copy_diagnostics)
        log_btn = QPushButton("Open Log")
        log_btn.clicked.connect(on_open_log)

        action_row = QHBoxLayout()
        action_row.addWidget(copy_btn)
        action_row.addWidget(log_btn)
        action_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addWidget(actions, stretch=1)
        layout.addLayout(action_row)
        layout.addWidget(buttons)


def _format_body(item: HelpItem) -> str:
    lines: list[str] = []
    if item.actions:
        lines.append("Suggested next steps:")
        for action in item.actions:
            lines.append(f"- {action}")
    if item.detail:
        if lines:
            lines.append("")
        lines.append("Last app error:")
        lines.append(item.detail)
    return "\n".join(lines) if lines else "No action needed."
