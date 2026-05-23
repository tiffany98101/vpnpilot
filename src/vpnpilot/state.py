"""Connection state model — shared across detection, CLI, and UI layers.

Auth and connection are orthogonal axes. A ConnectionInfo always carries
both. UNKNOWN auth means "haven't probed yet" / "probe failed"; UI
should not flip to a signed-out display on a single UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnState(Enum):
    DISCONNECTED = "disconnected"
    TRANSITIONING = "transitioning"
    CONNECTED = "connected"
    CLI_MISSING = "cli_missing"
    CLI_ERROR = "cli_error"
    NETWORK_OFFLINE = "network_offline"
    UNKNOWN = "unknown"


class AuthState(Enum):
    SIGNED_IN = "signed_in"
    SIGNED_OUT = "signed_out"
    UNKNOWN = "unknown"  # probe not yet run or transient failure


@dataclass(frozen=True)
class ConnectionInfo:
    """A snapshot of the connection. Fields are None when not applicable."""

    state: ConnState
    auth: AuthState = AuthState.UNKNOWN
    account_email: str | None = None
    server: str | None = None
    city: str | None = None
    country: str | None = None
    protocol: str | None = None
    load_percent: int | None = None
    public_ip: str | None = None
    interface: str | None = None
    backend: str | None = None
    backend_reason: str | None = None
    active_profile: str | None = None
    default_route_device: str | None = None
    dns_summary: str | None = None
    error: str | None = None
