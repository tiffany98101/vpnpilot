"""Connection state model — shared across detection, CLI, and UI layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnState(Enum):
    DISCONNECTED = "disconnected"
    TRANSITIONING = "transitioning"
    CONNECTED = "connected"
    CLI_MISSING = "cli_missing"
    NOT_SIGNED_IN = "not_signed_in"


@dataclass(frozen=True)
class ConnectionInfo:
    """A snapshot of the connection. Fields are None when not applicable."""

    state: ConnState
    server: str | None = None
    city: str | None = None
    country: str | None = None
    protocol: str | None = None
    load_percent: int | None = None
    public_ip: str | None = None
    interface: str | None = None
    error: str | None = None
