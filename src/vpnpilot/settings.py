"""User settings stored in ``~/.config/vpnpilot/settings.json``."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import config_dir

log = logging.getLogger(__name__)

BACKEND_AUTO = "auto"
BACKEND_PROTON_CLI = "proton-cli"
BACKEND_NETWORKMANAGER_OPENVPN = "networkmanager-openvpn"
BACKEND_CHOICES = frozenset(
    {
        BACKEND_AUTO,
        BACKEND_PROTON_CLI,
        BACKEND_NETWORKMANAGER_OPENVPN,
    }
)
DEFAULT_NMCLI_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class AppSettings:
    backend: str = BACKEND_AUTO
    networkmanager_profile: str = ""
    prefer_active_nm_vpn: bool = True
    nmcli_timeout_seconds: float = DEFAULT_NMCLI_TIMEOUT_SECONDS

    @classmethod
    def from_mapping(cls, data: dict) -> AppSettings:
        backend = data.get("backend", BACKEND_AUTO)
        if backend not in BACKEND_CHOICES:
            backend = BACKEND_AUTO
        profile = data.get("networkmanager_profile", "")
        if not isinstance(profile, str):
            profile = ""
        prefer_active = data.get("prefer_active_nm_vpn", True)
        if not isinstance(prefer_active, bool):
            prefer_active = True
        timeout = data.get("nmcli_timeout_seconds", DEFAULT_NMCLI_TIMEOUT_SECONDS)
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = DEFAULT_NMCLI_TIMEOUT_SECONDS
        timeout_f = min(max(timeout_f, 3.0), 120.0)
        return cls(
            backend=backend,
            networkmanager_profile=profile.strip(),
            prefer_active_nm_vpn=prefer_active,
            nmcli_timeout_seconds=timeout_f,
        )

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["version"] = 1
        return data


class SettingsStore:
    """Small JSON settings store. Values are user preferences, not secrets."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else config_dir() / "settings.json"
        self._cache: AppSettings | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        if self._cache is not None:
            return self._cache
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._cache = AppSettings()
            return self._cache
        except OSError as e:
            log.warning("could not read %s: %s", self._path, e)
            self._cache = AppSettings()
            return self._cache
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            log.warning("settings.json is malformed (%s); using defaults", e)
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._cache = AppSettings.from_mapping(data)
        return self._cache

    def save(self, settings: AppSettings) -> None:
        self._cache = settings
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create %s: %s", self._path.parent, e)
            return
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".settings.", suffix=".json.tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(settings.to_json_dict(), f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except OSError as e:
            log.warning("could not write %s: %s", self._path, e)
