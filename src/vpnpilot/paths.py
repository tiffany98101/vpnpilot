"""XDG path helpers for app-owned files."""

from __future__ import annotations

import os
from pathlib import Path


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def config_dir() -> Path:
    return xdg_config_home() / "vpnpilot"


def state_dir() -> Path:
    return xdg_state_home() / "vpnpilot"


def default_state_path() -> Path:
    return config_dir() / "state.json"


def default_presets_path() -> Path:
    return config_dir() / "presets.json"


def default_log_path() -> Path:
    return state_dir() / "vpnpilot.log"
