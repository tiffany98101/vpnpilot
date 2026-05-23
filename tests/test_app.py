from __future__ import annotations

from unittest.mock import MagicMock

from vpnpilot import app as app_mod
from vpnpilot.settings import AppSettings


class FakeApplication:
    def __init__(self, argv):
        self.argv = argv

    def setApplicationName(self, _name):
        pass

    def setApplicationDisplayName(self, _name):
        pass

    def setQuitOnLastWindowClosed(self, _enabled):
        pass

    def setWindowIcon(self, _icon):
        pass


def test_main_exits_success_when_tray_unavailable(monkeypatch):
    monkeypatch.setattr(app_mod, "SingletonLock", lambda: MagicMock(acquire=lambda: True))
    monkeypatch.setattr(app_mod, "QApplication", FakeApplication)
    monkeypatch.setattr(app_mod, "_app_icon", lambda: object())
    monkeypatch.setattr(app_mod, "ensure_tray_available", lambda _app: False)

    assert app_mod.main() == 0


def test_main_shows_clear_error_when_proton_cli_missing(monkeypatch):
    messages = []

    class MissingProtonCLI:
        @staticmethod
        def is_installed():
            return False

    monkeypatch.setattr(app_mod, "SingletonLock", lambda: MagicMock(acquire=lambda: True))
    monkeypatch.setattr(app_mod, "QApplication", FakeApplication)
    monkeypatch.setattr(app_mod, "_app_icon", lambda: object())
    monkeypatch.setattr(app_mod, "ensure_tray_available", lambda _app: True)
    monkeypatch.setattr(app_mod, "ProtonCLI", MissingProtonCLI)
    monkeypatch.setattr(app_mod, "_active_nm_vpn_exists", lambda: False)
    monkeypatch.setattr(app_mod, "SettingsStore", FakeSettingsStore)
    monkeypatch.setattr(
        app_mod.QMessageBox,
        "critical",
        lambda _parent, title, message: messages.append((title, message)),
    )

    assert app_mod.main() == 1
    assert len(messages) == 1
    assert "CLI not detected" in messages[0][0]
    assert "Proton VPN Linux CLI" in messages[0][1]
    assert "protonvpn" in messages[0][1]


class FakeLoop:
    def __init__(self, _app):
        self.delayed_calls = []

    def call_later(self, delay, callback, *args):
        self.delayed_calls.append((delay, callback, args))

    def run_forever(self):
        return 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class FakeController:
    instances = []

    def __init__(self, *args, **kwargs):
        self.state_changed = FakeSignal()
        self.started = False
        FakeController.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass


class FakeCatalog:
    instances = []

    def __init__(self, _cli):
        self.prewarm_called = False
        self.cities_calls = []
        FakeCatalog.instances.append(self)

    def prewarm(self):
        self.prewarm_called = True

    def cities(self, country_code):
        self.cities_calls.append(country_code)


class FakeTray:
    def __init__(self, *args, **kwargs):
        self.shown = False

    def show(self):
        self.shown = True


class FakeProtonCLI:
    @staticmethod
    def is_installed():
        return True


class FakeSettingsStore:
    def load(self):
        return AppSettings()


def test_main_does_not_prewarm_catalog_or_load_cities_on_startup(monkeypatch):
    FakeController.instances.clear()
    FakeCatalog.instances.clear()
    loops: list[FakeLoop] = []

    def make_loop(app):
        loop = FakeLoop(app)
        loops.append(loop)
        return loop

    monkeypatch.setattr(app_mod, "SingletonLock", lambda: MagicMock(acquire=lambda: True))
    monkeypatch.setattr(app_mod, "QApplication", FakeApplication)
    monkeypatch.setattr(app_mod, "_app_icon", lambda: object())
    monkeypatch.setattr(app_mod, "ensure_tray_available", lambda _app: True)
    monkeypatch.setattr(app_mod, "QEventLoop", make_loop)
    monkeypatch.setattr(app_mod.asyncio, "set_event_loop", lambda _loop: None)
    monkeypatch.setattr(app_mod.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_mod, "ProtonCLI", FakeProtonCLI)
    monkeypatch.setattr(app_mod, "_active_nm_vpn_exists", lambda: False)
    monkeypatch.setattr(app_mod, "SettingsStore", FakeSettingsStore)
    monkeypatch.setattr(app_mod, "default_detector", lambda _cli: object())
    monkeypatch.setattr(app_mod, "JsonStateStore", lambda: object())
    monkeypatch.setattr(app_mod, "PresetStore", lambda: MagicMock(load=lambda: None))
    monkeypatch.setattr(app_mod, "Controller", FakeController)
    monkeypatch.setattr(app_mod, "ServerCatalog", FakeCatalog)
    monkeypatch.setattr(app_mod, "TrayApp", FakeTray)

    assert app_mod.main() == 0

    assert len(FakeCatalog.instances) == 1
    assert FakeCatalog.instances[0].prewarm_called is False
    assert FakeCatalog.instances[0].cities_calls == []
    assert len(FakeController.instances[0].state_changed.callbacks) == 0
    assert loops[0].delayed_calls == [
        (app_mod.STARTUP_POLL_DELAY_SECONDS, FakeController.instances[0].start, ())
    ]
    assert FakeController.instances[0].started is False
