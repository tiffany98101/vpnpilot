"""Tests for the main window shell."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from vpnpilot.main_window import (
    MainWindow,
    PresetListModel,
    PresetListPanel,
    StatusPanel,
    preset_display,
    preset_matches_connection,
    target_summary,
)
from vpnpilot.preset import PresetFlags, PresetStore, PresetTarget, TargetKind
from vpnpilot.state import AuthState, ConnectionInfo, ConnState


class FakeController(QObject):
    """Stand-in for Controller — just the surface the main window uses."""

    state_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.current = ConnectionInfo(state=ConnState.DISCONNECTED)
        self.last_error = None
        self.disconnect = MagicMock()
        self.connect_preset = MagicMock()


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
    panel.render(ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT))
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
    assert win.setup_help_btn.text() == "Troubleshooting…"


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
    ctrl.state_changed.emit(ConnectionInfo(state=ConnState.CONNECTED, server="US-WA#187"))
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


# ---- target_summary / preset_display / preset_matches_connection -----


def test_target_summary_kinds():
    assert target_summary(PresetTarget(kind=TargetKind.NONE)) == "fastest available"
    assert target_summary(PresetTarget(kind=TargetKind.COUNTRY, value="US")) == "country US"
    assert (
        target_summary(PresetTarget(kind=TargetKind.CITY, value="Seattle"))
        == "city Seattle (best effort)"
    )
    assert (
        target_summary(PresetTarget(kind=TargetKind.SERVER_ID, value="US-WA#1")) == "server US-WA#1"
    )


def test_target_summary_city_with_country_scope():
    assert (
        target_summary(PresetTarget(kind=TargetKind.CITY, value="US::Seattle"))
        == "city Seattle (US, best effort)"
    )


def test_preset_display_includes_star_and_flags():
    from vpnpilot.preset import Preset

    p = Preset.new(
        name="Seattle",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
        flags=PresetFlags(p2p=True),
        is_default=True,
    )
    s = preset_display(p)
    assert "★" in s
    assert "Seattle" in s
    assert "P2P" in s


def test_preset_matches_connection_server_id():
    from vpnpilot.preset import Preset

    p = Preset.new(
        name="srv",
        target=PresetTarget(kind=TargetKind.SERVER_ID, value="US-WA#187"),
    )
    info = ConnectionInfo(state=ConnState.CONNECTED, server="US-WA#187")
    assert preset_matches_connection(p, info) is True
    info2 = ConnectionInfo(state=ConnState.CONNECTED, server="US-WA#999")
    assert preset_matches_connection(p, info2) is False


def test_preset_matches_connection_city_case_insensitive():
    from vpnpilot.preset import Preset

    p = Preset.new(
        name="ct",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
    )
    info = ConnectionInfo(state=ConnState.CONNECTED, server="X#1", city="seattle")
    assert preset_matches_connection(p, info) is True


def test_preset_matches_connection_city_with_scoped_value():
    from vpnpilot.preset import Preset

    p = Preset.new(
        name="ct",
        target=PresetTarget(kind=TargetKind.CITY, value="US::Seattle"),
    )
    info = ConnectionInfo(state=ConnState.CONNECTED, server="X#1", city="Seattle")
    assert preset_matches_connection(p, info) is True


def test_preset_matches_connection_none_kind_does_not_match():
    from vpnpilot.preset import Preset

    p = Preset.new(name="any", target=PresetTarget(kind=TargetKind.NONE))
    info = ConnectionInfo(state=ConnState.CONNECTED, server="X#1")
    assert preset_matches_connection(p, info) is False


def test_preset_matches_when_disconnected_is_false():
    from vpnpilot.preset import Preset

    p = Preset.new(
        name="srv",
        target=PresetTarget(kind=TargetKind.SERVER_ID, value="US-WA#187"),
    )
    info = ConnectionInfo(state=ConnState.DISCONNECTED)
    assert preset_matches_connection(p, info) is False


# ---- PresetListModel --------------------------------------------------


def test_model_initial_rows_match_store(qapp_instance, store):
    model = PresetListModel(store)
    assert model.rowCount() == 1  # seeded Seattle
    p = model.preset_at(0)
    assert p is not None and p.name == "Seattle"


def test_model_refresh_picks_up_new_preset(qapp_instance, store):
    model = PresetListModel(store)
    store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    model.refresh()
    assert model.rowCount() == 2
    names = [model.preset_at(i).name for i in range(model.rowCount())]
    assert names == ["Seattle", "NYC"]


# ---- PresetListPanel --------------------------------------------------


def _make_panel(qtbot, store, **overrides):
    callbacks = {
        "on_connect": MagicMock(),
        "on_edit": MagicMock(),
        "on_new": MagicMock(),
    }
    callbacks.update(overrides)
    panel = PresetListPanel(store, **callbacks)
    qtbot.addWidget(panel)
    return panel, callbacks


def test_panel_initial_selection_is_first_row(qapp_instance, qtbot, store):
    panel, _ = _make_panel(qtbot, store)
    p = panel._selected_preset()
    assert p is not None and p.name == "Seattle"


def test_panel_connect_button_calls_callback(qapp_instance, qtbot, store):
    panel, cbs = _make_panel(qtbot, store)
    panel.update_for_connection(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_IN)
    )
    panel.connect_btn.click()
    cbs["on_connect"].assert_called_once()
    assert cbs["on_connect"].call_args[0][0].name == "Seattle"


def test_panel_connect_disabled_when_signed_out(qapp_instance, qtbot, store):
    panel, _ = _make_panel(qtbot, store)
    panel.update_for_connection(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)
    )
    assert panel.connect_btn.isEnabled() is False


def test_panel_connect_disabled_when_already_at_target(qapp_instance, qtbot, store):
    panel, _ = _make_panel(qtbot, store)
    # Seattle preset has target city=Seattle; connected city=Seattle ⇒ match.
    panel.update_for_connection(
        ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=AuthState.SIGNED_IN,
            server="US-WA#187",
            city="Seattle",
        )
    )
    assert panel.connect_btn.isEnabled() is False


def test_panel_double_click_connects(qapp_instance, qtbot, store):
    panel, cbs = _make_panel(qtbot, store)
    panel.update_for_connection(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_IN)
    )
    idx = panel._model.index(0, 0)
    panel._on_double_click(idx)
    cbs["on_connect"].assert_called_once()


def test_panel_delete_default_button_disabled(qapp_instance, qtbot, store):
    panel, _ = _make_panel(qtbot, store)
    assert panel.delete_btn.isEnabled() is False


def test_panel_delete_non_default(qapp_instance, qtbot, store, monkeypatch):
    nyc = store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    panel, _ = _make_panel(qtbot, store)
    panel.refresh()
    # Select NYC (row 1 after seed).
    panel.list_view.setCurrentIndex(panel._model.index(1, 0))
    assert panel.delete_btn.isEnabled() is True
    # Auto-accept the confirmation dialog.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes),
    )
    panel.delete_btn.click()
    assert store.get(nyc.id) is None
    assert panel._model.rowCount() == 1


def test_panel_set_default_promotes_and_moves_to_top(qapp_instance, qtbot, store):
    nyc = store.add(name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York"))
    panel, _ = _make_panel(qtbot, store)
    panel.refresh()
    panel.list_view.setCurrentIndex(panel._model.index(1, 0))
    assert panel.set_default_btn.isEnabled() is True
    panel.set_default_btn.click()
    # NYC is now default and at index 0.
    first = panel._model.preset_at(0)
    assert first.id == nyc.id
    assert first.is_default is True


def test_panel_new_button_calls_callback(qapp_instance, qtbot, store):
    panel, cbs = _make_panel(qtbot, store)
    panel.new_btn.click()
    cbs["on_new"].assert_called_once()


def test_panel_edit_button_calls_callback(qapp_instance, qtbot, store):
    panel, cbs = _make_panel(qtbot, store)
    panel.edit_btn.click()
    cbs["on_edit"].assert_called_once()


def test_main_window_routes_preset_connect_to_controller(qapp_instance, qtbot, store):
    ctrl = FakeController()
    win = MainWindow(ctrl, store)
    qtbot.addWidget(win)
    win.preset_panel.update_for_connection(
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_IN)
    )
    win.preset_panel.connect_btn.click()
    ctrl.connect_preset.assert_called_once()
    seattle = store.list_all()[0]
    assert ctrl.connect_preset.call_args[0][0] == seattle.id
