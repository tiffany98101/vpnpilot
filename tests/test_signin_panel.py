"""Tests for the sign-in panel."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from vpnpilot.signin_panel import EMAIL_PLACEHOLDER, SignInPanel, build_signin_command
from vpnpilot.state import AuthState, ConnectionInfo, ConnState


class FakeSignalSource(QObject):
    state_changed = pyqtSignal(object)


@pytest.fixture
def qapp_instance():
    return QApplication.instance() or QApplication([])


def test_build_signin_command_uses_last_email():
    assert build_signin_command("a@b.com") == "protonvpn signin a@b.com"


def test_build_signin_command_falls_back_to_placeholder():
    assert build_signin_command(None) == f"protonvpn signin {EMAIL_PLACEHOLDER}"
    assert build_signin_command("") == f"protonvpn signin {EMAIL_PLACEHOLDER}"


def test_panel_shows_email_in_command(qapp_instance, qtbot):
    src = FakeSignalSource()
    panel = SignInPanel(
        last_email="user@example.com",
        on_recheck=lambda: None,
        state_signal=src.state_changed,
    )
    qtbot.addWidget(panel)
    assert panel.command_text() == "protonvpn signin user@example.com"


def test_copy_button_writes_command_to_clipboard(qapp_instance, qtbot):
    from PyQt6.QtWidgets import QPushButton

    src = FakeSignalSource()
    panel = SignInPanel(
        last_email="user@example.com",
        on_recheck=lambda: None,
        state_signal=src.state_changed,
    )
    qtbot.addWidget(panel)
    btn = panel.findChild(QPushButton, "copyButton")
    assert btn is not None
    btn.click()
    cb = qapp_instance.clipboard()
    assert cb.text() == "protonvpn signin user@example.com"


def test_recheck_button_invokes_callback(qapp_instance, qtbot):
    src = FakeSignalSource()
    callback = MagicMock()
    panel = SignInPanel(
        last_email=None,
        on_recheck=callback,
        state_signal=src.state_changed,
    )
    qtbot.addWidget(panel)
    from PyQt6.QtWidgets import QPushButton

    btn = panel.findChild(QPushButton, "recheckButton")
    assert btn is not None
    btn.click()
    callback.assert_called_once()


def test_panel_closes_on_signed_in_state(qapp_instance, qtbot):
    src = FakeSignalSource()
    panel = SignInPanel(
        last_email=None,
        on_recheck=lambda: None,
        state_signal=src.state_changed,
    )
    qtbot.addWidget(panel)
    panel.show()
    assert panel.isVisible()
    # Still signed out: nothing happens.
    src.state_changed.emit(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)
    )
    assert panel.isVisible()
    # Signed in: panel auto-closes.
    src.state_changed.emit(
        ConnectionInfo(
            state=ConnState.DISCONNECTED,
            auth=AuthState.SIGNED_IN,
            account_email="user@example.com",
        )
    )
    assert not panel.isVisible()


def test_panel_does_not_close_on_unknown_auth(qapp_instance, qtbot):
    src = FakeSignalSource()
    panel = SignInPanel(
        last_email=None,
        on_recheck=lambda: None,
        state_signal=src.state_changed,
    )
    qtbot.addWidget(panel)
    panel.show()
    src.state_changed.emit(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.UNKNOWN)
    )
    assert panel.isVisible()
