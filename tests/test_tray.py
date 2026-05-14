"""Tests for the dynamic tray menu.

We don't actually display the tray icon — QSystemTrayIcon is happy to
be instantiated in headless tests, and the menu construction logic is
what matters.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu

from vpnpilot.preset import PresetStore, PresetTarget, TargetKind
from vpnpilot.state import AuthState, ConnectionInfo, ConnState
from vpnpilot.tray import TrayApp


class FakeController(QObject):
    state_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.current = ConnectionInfo(state=ConnState.DISCONNECTED)
        self.connect_preset = MagicMock()
        self.disconnect = MagicMock()
        self.force_refresh = MagicMock()
        self.stop = MagicMock()


@pytest.fixture
def qapp_instance():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    s = PresetStore(path=tmp_path / "presets.json")
    s.load()
    return s


def _connect_action_texts(tray: TrayApp) -> list[str]:
    return [a.text() for a in tray._dynamic_actions]


def test_initial_menu_has_default_connect_only_with_one_preset(
    qapp_instance, store
):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    assert _connect_action_texts(tray) == ["Connect to Seattle"]
    assert tray._connect_submenu is None


def test_menu_grows_submenu_when_more_presets(qapp_instance, store):
    store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    store.add(name="LA", target=PresetTarget(kind=TargetKind.CITY, value="Los Angeles"))
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    # Top action is the default; submenu has the other two.
    assert _connect_action_texts(tray)[0] == "Connect to Seattle"
    assert tray._connect_submenu is not None
    submenu_texts = [a.text() for a in tray._connect_submenu.actions()]
    assert set(submenu_texts) == {"NYC", "LA"}


def test_clicking_default_connect_routes_to_controller(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    seattle = store.list_all()[0]
    tray._dynamic_actions[0].trigger()
    ctrl.connect_preset.assert_called_once_with(seattle.id)


def test_clicking_submenu_item_routes_to_controller(qapp_instance, store):
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    assert tray._connect_submenu is not None
    nyc_action = next(
        a for a in tray._connect_submenu.actions() if a.text() == "NYC"
    )
    nyc_action.trigger()
    ctrl.connect_preset.assert_called_once_with(nyc.id)


def test_rebuild_after_preset_added(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    assert len(tray._dynamic_actions) == 1
    assert tray._connect_submenu is None
    store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    tray._rebuild_connect_section()
    assert tray._connect_submenu is not None
    assert _connect_action_texts(tray)[0] == "Connect to Seattle"


def test_rebuild_after_set_default_swaps_top_entry(qapp_instance, store):
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    assert _connect_action_texts(tray)[0] == "Connect to Seattle"
    store.set_default(nyc.id)
    tray._rebuild_connect_section()
    assert _connect_action_texts(tray)[0] == "Connect to NYC"


def test_dynamic_actions_disabled_when_signed_out(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    ctrl.state_changed.emit(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)
    )
    for a in tray._dynamic_actions:
        assert a.isEnabled() is False


def test_disconnect_stays_enabled_when_connected_even_if_signed_out(
    qapp_instance, store
):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    ctrl.state_changed.emit(
        ConnectionInfo(state=ConnState.CONNECTED, auth=AuthState.SIGNED_OUT)
    )
    assert tray._disconnect_action.isEnabled() is True


def test_dynamic_actions_disabled_when_connected(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    ctrl.state_changed.emit(
        ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=AuthState.SIGNED_IN,
            server="US-WA#187",
        )
    )
    for a in tray._dynamic_actions:
        assert a.isEnabled() is False


def test_dynamic_actions_enabled_when_disconnected(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    ctrl.state_changed.emit(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_IN)
    )
    for a in tray._dynamic_actions:
        assert a.isEnabled() is True


def test_menu_structure_has_expected_top_level_items(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    # Top-level menu has separators that don't carry text; non-empty
    # text actions in order should be sensible.
    texts = [a.text() for a in tray._menu.actions() if a.text()]
    # Sign in… is hidden by default; status text varies; check the
    # named anchor items are present.
    assert any("Open VPNPilot" in t for t in texts)
    assert "Connect to Seattle" in texts
    assert "Disconnect" in texts
    assert "Quit" in texts


def test_about_to_show_signal_triggers_rebuild(qapp_instance, store):
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    initial_count = len(tray._dynamic_actions)
    # Add a preset and emit aboutToShow — should rebuild.
    store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    tray._menu.aboutToShow.emit()
    assert tray._connect_submenu is not None
    assert len(tray._dynamic_actions) > initial_count


def test_disconnect_action_visible_in_menu(qapp_instance, store):
    # Sanity: Disconnect is still where it was, separately from the
    # dynamic section.
    ctrl = FakeController()
    tray = TrayApp(qapp_instance, ctrl, preset_store=store)
    assert isinstance(tray._menu, QMenu)
    assert any(a.text() == "Disconnect" for a in tray._menu.actions())
