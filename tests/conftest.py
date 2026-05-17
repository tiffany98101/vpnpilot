from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg_dirs(tmp_path, monkeypatch):
    """Keep tests out of the real user's config/state/data locations."""
    home = tmp_path / "home"
    config = tmp_path / "config"
    state = tmp_path / "state"
    cache = tmp_path / "cache"
    data = tmp_path / "data"
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, mode=0o700)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    yield

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_vpnpilot_log_handler", False):
            root.removeHandler(handler)
            handler.close()
