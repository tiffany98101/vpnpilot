from __future__ import annotations

import logging
import os
import stat
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vpnpilot.logging_setup import configure_logging
from vpnpilot.paths import default_log_path, default_presets_path, default_state_path
from vpnpilot.preset import PresetStore
from vpnpilot.user_state import JsonStateStore


def test_log_path_respects_xdg_state_home(tmp_path, monkeypatch):
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    assert default_log_path() == state_home / "vpnpilot" / "vpnpilot.log"

    configure_logging()
    logging.getLogger("vpnpilot.test").warning("test log line")

    assert default_log_path().exists()
    assert default_log_path().is_relative_to(state_home)


def test_default_config_paths_respect_xdg_config_home(tmp_path, monkeypatch):
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert default_presets_path() == config_home / "vpnpilot" / "presets.json"
    assert default_state_path() == config_home / "vpnpilot" / "state.json"

    presets = PresetStore()
    presets.load()
    state = JsonStateStore()
    state.note_email("user@example.com")

    assert default_presets_path().exists()
    assert default_state_path().exists()
    assert default_presets_path().is_relative_to(config_home)
    assert default_state_path().is_relative_to(config_home)


def test_xdg_overrides_do_not_touch_home_fallback(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    state_home = tmp_path / "state"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    PresetStore().load()
    JsonStateStore().set_poll_interval_key("manual")
    configure_logging()
    logging.getLogger("vpnpilot.test").warning("isolated")

    assert not (home / ".config" / "vpnpilot").exists()
    assert not (home / ".local" / "state" / "vpnpilot").exists()
    assert (config_home / "vpnpilot" / "presets.json").exists()
    assert (config_home / "vpnpilot" / "state.json").exists()
    assert (state_home / "vpnpilot" / "vpnpilot.log").exists()


def test_configure_logging_retargets_when_xdg_state_home_changes(tmp_path, monkeypatch):
    first = tmp_path / "state-1"
    second = tmp_path / "state-2"

    monkeypatch.setenv("XDG_STATE_HOME", str(first))
    configure_logging()
    monkeypatch.setenv("XDG_STATE_HOME", str(second))
    configure_logging()

    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_vpnpilot_log_handler", False)
    ]
    assert len(handlers) == 1
    assert isinstance(handlers[0], RotatingFileHandler)
    assert Path(handlers[0].baseFilename) == second / "vpnpilot" / "vpnpilot.log"


def test_log_file_and_rotations_are_private(tmp_path, monkeypatch):
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    old_umask = os.umask(0)
    try:
        configure_logging(max_bytes=80, backup_count=1)
        logger = logging.getLogger("vpnpilot.test.rotation")
        for i in range(10):
            logger.warning("rotation line %s with enough bytes to roll over", i)
    finally:
        os.umask(old_umask)

    current = default_log_path()
    rotated = current.with_name(current.name + ".1")
    assert stat.S_IMODE(current.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
    assert stat.S_IMODE(rotated.stat().st_mode) == 0o600
