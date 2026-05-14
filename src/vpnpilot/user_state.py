"""On-disk observed-state cache.

This is **observed** state (what we last saw the CLI report), not user
preferences. Settings live elsewhere (see CLAUDE.md "Settings &
persistence"). The file is small JSON in XDG_CONFIG_HOME:
~/.config/vpnpilot/state.json.

We persist this so the sign-in panel can pre-fill the user's email
without prompting after a fresh launch.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


def _xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(raw)


def default_state_path() -> Path:
    return _xdg_config_home() / "vpnpilot" / "state.json"


class Persistence(Protocol):
    """What the controller is allowed to ask of state storage."""

    def note_email(self, email: str | None) -> None: ...
    def last_email(self) -> str | None: ...


class NullPersistence:
    """No-op Persistence — used in tests and when disk is unavailable."""

    def note_email(self, email: str | None) -> None:
        return

    def last_email(self) -> str | None:
        return None


class JsonStateStore:
    """File-backed state. Atomic writes, tolerant of missing/corrupt files."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else default_state_path()
        self._cache: dict | None = None

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._cache = {}
            return self._cache
        except OSError as e:
            log.warning("could not read %s: %s", self._path, e)
            self._cache = {}
            return self._cache
        try:
            self._cache = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            log.warning("state.json is malformed (%s); ignoring", e)
            self._cache = {}
        return self._cache

    def _write(self, data: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create %s: %s", self._path.parent, e)
            return
        try:
            # Atomic write: tmp + rename, on the same directory so rename is atomic.
            fd, tmp = tempfile.mkstemp(
                prefix=".state.", suffix=".json.tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except OSError as e:
            log.warning("could not write %s: %s", self._path, e)

    def last_email(self) -> str | None:
        return self._load().get("last_email")

    def note_email(self, email: str | None) -> None:
        if not email:
            return
        data = self._load()
        if data.get("last_email") == email:
            return
        data = dict(data, last_email=email)
        self._cache = data
        self._write(data)
