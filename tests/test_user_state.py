"""Tests for the on-disk observed-state cache."""

from __future__ import annotations

from vpnpilot.settings import AppSettings, SettingsStore
from vpnpilot.user_state import JsonStateStore, NullPersistence


def test_null_persistence_no_ops(tmp_path):
    p = NullPersistence()
    p.note_email("a@example.com")
    p.set_poll_interval_key("5m")
    assert p.last_email() is None
    assert p.poll_interval_key() is None


def test_json_store_round_trip(tmp_path):
    path = tmp_path / "state.json"
    s = JsonStateStore(path=path)
    assert s.last_email() is None
    s.note_email("a@example.com")
    assert s.last_email() == "a@example.com"
    # Fresh instance reads back from disk.
    s2 = JsonStateStore(path=path)
    assert s2.last_email() == "a@example.com"


def test_json_store_ignores_blank_email(tmp_path):
    path = tmp_path / "state.json"
    s = JsonStateStore(path=path)
    s.note_email(None)
    s.note_email("")
    assert s.last_email() is None
    assert not path.exists()


def test_json_store_atomic_no_dup_writes_when_unchanged(tmp_path):
    path = tmp_path / "state.json"
    s = JsonStateStore(path=path)
    s.note_email("a@example.com")
    mtime1 = path.stat().st_mtime_ns
    s.note_email("a@example.com")  # same value
    mtime2 = path.stat().st_mtime_ns
    assert mtime1 == mtime2  # no rewrite


def test_json_store_tolerates_malformed_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{this is not json")
    s = JsonStateStore(path=path)
    assert s.last_email() is None
    s.note_email("a@example.com")
    assert s.last_email() == "a@example.com"


def test_json_store_creates_parent_dir(tmp_path):
    path = tmp_path / "deeply" / "nested" / "state.json"
    s = JsonStateStore(path=path)
    s.note_email("a@example.com")
    assert path.exists()


def test_json_store_file_permission_user_only(tmp_path):
    path = tmp_path / "state.json"
    s = JsonStateStore(path=path)
    s.note_email("a@example.com")
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_json_store_poll_interval_round_trip(tmp_path):
    path = tmp_path / "state.json"
    s = JsonStateStore(path=path)
    assert s.poll_interval_key() is None
    s.set_poll_interval_key("5m")
    assert s.poll_interval_key() == "5m"
    assert JsonStateStore(path=path).poll_interval_key() == "5m"


def test_settings_store_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    s = SettingsStore(path=path)
    settings = AppSettings(
        backend="networkmanager-openvpn",
        networkmanager_profile="us-dc-281.protonvpn.tcp",
    )
    s.save(settings)

    loaded = SettingsStore(path=path).load()
    assert loaded.backend == "networkmanager-openvpn"
    assert loaded.networkmanager_profile == "us-dc-281.protonvpn.tcp"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_settings_store_sanitizes_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{"backend": "bad", "networkmanager_profile": 12, "nmcli_timeout_seconds": 0}',
        encoding="utf-8",
    )
    loaded = SettingsStore(path=path).load()
    assert loaded.backend == "auto"
    assert loaded.networkmanager_profile == ""
    assert loaded.nmcli_timeout_seconds == 3.0
