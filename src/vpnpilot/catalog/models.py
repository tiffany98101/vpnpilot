"""Data models for the server catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum


class CityFeature(StrEnum):
    P2P = "P2P"
    TOR = "Tor"


class EntryState(Enum):
    NOT_FETCHED = "not_fetched"
    LOADING = "loading"
    LOADED = "loaded"
    FAILED = "failed"


@dataclass(frozen=True)
class Country:
    code: str
    name: str


@dataclass(frozen=True)
class City:
    name: str
    country_code: str
    features: frozenset[CityFeature] = field(default_factory=frozenset)


@dataclass
class CatalogEntry:
    country_code: str
    state: EntryState = EntryState.NOT_FETCHED
    cities: list[City] = field(default_factory=list)
    last_error: str | None = None
    last_fetched_at: datetime | None = None


class CatalogError(Exception):
    pass
