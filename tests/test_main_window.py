"""Tests for the main window shell."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from vpnpilot.main_window import MainWindow, StatusPanel
from vpnpilot.preset import PresetStore
from vpnpilot.state import AuthState, ConnectionInfo, ConnState


class FakeController(QObject):
    """Stand-in for Controller — just the surface the main window uses."""

    state_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.current = ConnectionInfo(state=ConnState.DISCONNECTED)
        self.disconnect = MagicMock()


@pytest.fixture
def qapp_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    s = PresetStore(path=tmp_path / "presets.json")
    s.load()
    return s


# ---- StatusPanel rendering -------------------------------------------


def test_status_panel_initial_state_is_disconnected(qapp_instance, qtbot):
    panel = StatusPanel()
    qtbot.addWidget(panel)
    assert "Disconnected" in panel.state_label.text()
    assert not panel.server_label.isVisible()
    assert not panel.auth_label.isVisible()


def test_status_panel_connected_shows_server_and_protocol(qapp_instance, qtbot):
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.show()  # visibility checks need the parent shown
    panel.render(
        ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=AuthState.SIGNED_IN,
            server="US-WA#187",
            city="Seattle",
            country="United States",
            protocol="wireguard",
        )
    )
    assert "Connected" in panel.state_label.text()
    assert "US-WA#187" in panel.server_label.text()
    assert "Seattle" in panel.server_label.text()
    assert panel.server_label.isVisible()
    assert "wireguard" in panel.protocol_label.text()
    assert panel.protocol_label.isVisible()
    assert not panel.auth_label.isVisible()


def test_status_panel_signed_out_shows_auth_indicator(qapp_instance, qtbot):
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.render(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)
    )
    assert panel.auth_label.isVisible()


def test_status_panel_transitioning(qapp_instance, qtbot):
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.render(ConnectionInfo(state=ConnState.TRANSITIONING))
    assert "Working" in panel.state_label.text()


# ---- MainWindow integration -----------------------------------------


def test_main_window_renders_initial_state(qapp_instance, qtbot, store):
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    assert "Disconnected" in win.status_panel.state_label.text()
    assert win.disconnect_btn.isEnabled() is False


def test_main_window_enables_disconnect_when_connected(qapp_instance, qtbot, store):
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    ctrl.state_changed.emit(
        ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=AuthState.SIGNED_IN,
            server="US-WA#187",
        )
    )
    assert win.disconnect_btn.isEnabled()


def test_main_window_disconnect_button_calls_controller(qapp_instance, qtbot, store):
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    ctrl.state_changed.emit(
        ConnectionInfo(state=ConnState.CONNECTED, server="US-WA#187")
    )
    win.disconnect_btn.click()
    ctrl.disconnect.assert_called_once()


def test_disconnect_stays_enabled_even_when_signed_out(qapp_instance, qtbot, store):
    # Recovery path: if we observe CONNECTED + SIGNED_OUT (rare, but
    # possible if the user signed out from another tool while a tunnel
    # is up), Disconnect must remain reachable.
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    ctrl.state_changed.emit(
        ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=AuthState.SIGNED_OUT,
            server="US-WA#187",
        )
    )
    assert win.disconnect_btn.isEnabled()


def test_main_window_close_hides_instead_of_destroying(qapp_instance, qtbot, store):
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    win.show()
    assert win.isVisible()
    win.close()
    assert not win.isVisible()
    # Window still exists — can be reshown.
    win.show()
    assert win.isVisible()
