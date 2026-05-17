"""Preset model + on-disk store.

A preset is a saved 'how I want to connect' shape. The schema keeps
target (where) orthogonal from flags (how), matching the empirical CLI
surface in docs/cli-reference.md.

Storage: ~/.config/vpnpilot/presets.json (XDG_CONFIG_HOME). 0600 perms,
atomic tmp+rename. Corrupt files are renamed to <path>.corrupt and the
store reseeds from SEED_PRESET.

Invariants enforced by PresetStore:
    - At least one preset exists (seeded on first load if absent).
    - Exactly one preset has is_default=True (auto-repaired on load).
    - Preset names are unique (case-sensitive).
    - The default preset is always at index 0 in list_all().

Constructing a PresetTarget or PresetFlags with an invalid shape
raises ValueError — see __post_init__ in each.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from .paths import default_presets_path

log = logging.getLogger(__name__)


class TargetKind(StrEnum):
    NONE = "none"
    COUNTRY = "country"
    CITY = "city"
    SERVER_ID = "server_id"


@dataclass(frozen=True)
class PresetTarget:
    kind: TargetKind
    value: str = ""

    def __post_init__(self) -> None:
        if self.kind is TargetKind.NONE:
            if self.value:
                object.__setattr__(self, "value", "")
            return
        if not self.value.strip():
            raise ValueError(f"target kind {self.kind.value} requires a non-empty value")


@dataclass(frozen=True)
class PresetFlags:
    p2p: bool = False
    secure_core: bool = False
    tor: bool = False
    random: bool = False

    def __post_init__(self) -> None:
        # p2p / secure_core / tor each name a distinct server pool and
        # don't combine. `random` is orthogonal (random P2P / random
        # Secure Core / random Tor are all coherent).
        modifiers = (self.p2p, self.secure_core, self.tor)
        if sum(1 for b in modifiers if b) > 1:
            raise ValueError("at most one of p2p / secure_core / tor may be set")


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    target: PresetTarget
    flags: PresetFlags = field(default_factory=PresetFlags)
    is_default: bool = False

    @classmethod
    def new(
        cls,
        name: str,
        target: PresetTarget,
        flags: PresetFlags | None = None,
        *,
        is_default: bool = False,
    ) -> Preset:
        return cls(
            id=uuid.uuid4().hex,
            name=name,
            target=target,
            flags=flags or PresetFlags(),
            is_default=is_default,
        )


def _seed_preset() -> Preset:
    return Preset.new(
        name="Seattle",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
        is_default=True,
    )


# ---- serialization ----------------------------------------------------

_STORE_VERSION = 1
_CITY_SCOPE_SEP = "::"
_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")


def encode_city_target(city: str, country_code: str | None = None) -> str:
    """Encode a city target with optional country scope metadata.

    Stored format is "<CC>::<City>" when a 2-letter country code is
    present; legacy unscoped values remain plain "<City>".
    """
    clean_city = city.strip()
    if not clean_city:
        raise ValueError("city target requires a non-empty city")
    clean_code = (country_code or "").strip().upper()
    if clean_code and _COUNTRY_CODE_RE.fullmatch(clean_code):
        return f"{clean_code}{_CITY_SCOPE_SEP}{clean_city}"
    return clean_city


def decode_city_target(value: str) -> tuple[str | None, str]:
    """Decode a city target value into (country_code, city).

    Accepts both scoped values ("US::Seattle") and legacy unscoped values
    ("Seattle"). Malformed scoped values are treated as unscoped.
    """
    raw = value.strip()
    if not raw:
        return None, ""
    head, sep, tail = raw.partition(_CITY_SCOPE_SEP)
    if sep:
        code = head.strip().upper()
        city = tail.strip()
        if city and _COUNTRY_CODE_RE.fullmatch(code):
            return code, city
    return None, raw


def preset_to_dict(p: Preset) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "target": {"kind": p.target.kind.value, "value": p.target.value},
        "flags": {
            "p2p": p.flags.p2p,
            "secure_core": p.flags.secure_core,
            "tor": p.flags.tor,
            "random": p.flags.random,
        },
        "is_default": p.is_default,
    }


def preset_from_dict(d: dict) -> Preset:
    target_d = d["target"]
    flags_d = d.get("flags", {})
    return Preset(
        id=str(d["id"]),
        name=str(d["name"]),
        target=PresetTarget(
            kind=TargetKind(target_d["kind"]),
            value=str(target_d.get("value", "")),
        ),
        flags=PresetFlags(
            p2p=bool(flags_d.get("p2p", False)),
            secure_core=bool(flags_d.get("secure_core", False)),
            tor=bool(flags_d.get("tor", False)),
            random=bool(flags_d.get("random", False)),
        ),
        is_default=bool(d.get("is_default", False)),
    )


# ---- store ------------------------------------------------------------


class PresetStore:
    """File-backed preset list. See module docstring for invariants."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else default_presets_path()
        self._presets: list[Preset] = []
        self._loaded = False

    # ----- public -----

    def load(self) -> list[Preset]:
        if self._loaded:
            return self.list_all()
        self._presets = self._read_or_seed()
        self._enforce_invariants_on_load()
        self._loaded = True
        return self.list_all()

    def list_all(self) -> list[Preset]:
        return list(self._presets)

    def get(self, preset_id: str) -> Preset | None:
        for p in self._presets:
            if p.id == preset_id:
                return p
        return None

    def default(self) -> Preset:
        for p in self._presets:
            if p.is_default:
                return p
        # Invariant guarantees this is unreachable in practice. If
        # somehow violated at runtime, fall back to the first preset
        # rather than crash — the UI is still useful.
        return self._presets[0]

    def add(
        self,
        name: str,
        target: PresetTarget,
        flags: PresetFlags | None = None,
    ) -> Preset:
        self._require_loaded()
        self._require_valid_unique_name(name, exclude_id=None)
        preset = Preset.new(name=name, target=target, flags=flags)
        # If somehow no defaults exist (shouldn't happen post-seed),
        # the first add becomes default. Otherwise it appends normally.
        if not any(p.is_default for p in self._presets):
            preset = replace(preset, is_default=True)
        self._presets.append(preset)
        self._save()
        return preset

    def update(
        self,
        preset_id: str,
        *,
        name: str | None = None,
        target: PresetTarget | None = None,
        flags: PresetFlags | None = None,
    ) -> Preset:
        self._require_loaded()
        idx, existing = self._index_of(preset_id)
        new_name = existing.name if name is None else name
        if new_name != existing.name:
            self._require_valid_unique_name(new_name, exclude_id=preset_id)
        updated = replace(
            existing,
            name=new_name,
            target=existing.target if target is None else target,
            flags=existing.flags if flags is None else flags,
        )
        self._presets[idx] = updated
        self._save()
        return updated

    def delete(self, preset_id: str) -> None:
        self._require_loaded()
        idx, existing = self._index_of(preset_id)
        if existing.is_default:
            raise ValueError(
                "cannot delete the default preset; set another preset as default first"
            )
        del self._presets[idx]
        self._save()

    def set_default(self, preset_id: str) -> Preset:
        self._require_loaded()
        idx, target = self._index_of(preset_id)
        if target.is_default:
            return target
        self._presets = [replace(p, is_default=(i == idx)) for i, p in enumerate(self._presets)]
        new_default = self._presets.pop(idx)
        self._presets.insert(0, new_default)
        self._save()
        return new_default

    # ----- internals -----

    def _require_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _require_valid_unique_name(self, name: str, *, exclude_id: str | None) -> None:
        if not name.strip():
            raise ValueError("preset name must be non-empty")
        for p in self._presets:
            if p.id != exclude_id and p.name == name:
                raise ValueError(f"preset name {name!r} already exists")

    def _index_of(self, preset_id: str) -> tuple[int, Preset]:
        for i, p in enumerate(self._presets):
            if p.id == preset_id:
                return i, p
        raise KeyError(preset_id)

    def _read_or_seed(self) -> list[Preset]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.debug("presets.json absent; seeding with default preset")
            seeded = [_seed_preset()]
            self._presets = seeded
            self._save_now(seeded)
            return seeded
        except OSError as e:
            log.warning("could not read %s: %s; using in-memory seed", self._path, e)
            return [_seed_preset()]

        if not raw.strip():
            log.debug("presets.json is empty; reseeding")
            seeded = [_seed_preset()]
            self._presets = seeded
            self._save_now(seeded)
            return seeded

        try:
            doc = json.loads(raw)
            presets_d = doc.get("presets", [])
            parsed = [preset_from_dict(d) for d in presets_d]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            log.warning("presets.json is malformed (%s); backing up to .corrupt and reseeding", e)
            self._backup_corrupt()
            seeded = [_seed_preset()]
            self._presets = seeded
            self._save_now(seeded)
            return seeded

        if not parsed:
            seeded = [_seed_preset()]
            self._presets = seeded
            self._save_now(seeded)
            return seeded
        return parsed

    def _backup_corrupt(self) -> None:
        corrupt = self._path.with_name(self._path.name + ".corrupt")
        try:
            os.replace(self._path, corrupt)
        except OSError as e:
            log.warning("could not move %s to %s: %s", self._path, corrupt, e)

    def _enforce_invariants_on_load(self) -> None:
        if not self._presets:
            self._presets = [_seed_preset()]
            self._save()
            return
        # Repair: exactly one default. Prefer the first one flagged
        # as default; if none are, promote index 0.
        flagged = [i for i, p in enumerate(self._presets) if p.is_default]
        needs_rewrite = False
        if len(flagged) == 1:
            new_default_idx = flagged[0]
        else:
            new_default_idx = flagged[0] if flagged else 0
            self._presets = [
                replace(p, is_default=(i == new_default_idx)) for i, p in enumerate(self._presets)
            ]
            needs_rewrite = True
        # Move default to index 0 if not already there.
        if new_default_idx != 0:
            top = self._presets.pop(new_default_idx)
            self._presets.insert(0, top)
            needs_rewrite = True
        if needs_rewrite:
            self._save()

    def _save(self) -> None:
        self._save_now(self._presets)

    def _save_now(self, presets: list[Preset]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create %s: %s", self._path.parent, e)
            return
        doc = {
            "version": _STORE_VERSION,
            "presets": [preset_to_dict(p) for p in presets],
        }
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".presets.", suffix=".json.tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except OSError as e:
            log.warning("could not write %s: %s", self._path, e)


# ---- argv mapping (matches the captured CLI surface) -----------------


def preset_to_connect_kwargs(preset: Preset) -> dict:
    """Translate a Preset into kwargs for ProtonCLI.connect().

    Matches the verified argv mapping in docs/cli-reference.md "Connect
    surface → Implication for the preset model". The kwarg names here
    follow the existing ProtonCLI.connect signature (city/country/
    server_id/p2p/secure_core/tor/random_server).
    """
    kwargs: dict = {}
    t = preset.target
    if t.kind is TargetKind.COUNTRY:
        kwargs["country"] = t.value
    elif t.kind is TargetKind.CITY:
        _country_code, city = decode_city_target(t.value)
        kwargs["city"] = city
    elif t.kind is TargetKind.SERVER_ID:
        kwargs["server_id"] = t.value
    f = preset.flags
    if f.p2p:
        kwargs["p2p"] = True
    if f.secure_core:
        kwargs["secure_core"] = True
    if f.tor:
        kwargs["tor"] = True
    if f.random:
        kwargs["random_server"] = True
    return kwargs
