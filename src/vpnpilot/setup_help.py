"""User-facing setup and troubleshooting guidance."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .cli import PROTONVPN_BIN, PROTONVPN_RPM_PACKAGE
from .diagnostics import redact
from .state import AuthState, ConnectionInfo, ConnState


@dataclass(frozen=True)
class HelpItem:
    title: str
    message: str
    actions: tuple[str, ...] = ()
    detail: str | None = None


def help_for_status(
    info: ConnectionInfo,
    *,
    last_error: str | None = None,
    has_presets: bool = True,
    has_nmcli: bool | None = None,
    has_resolvectl: bool | None = None,
) -> HelpItem:
    """Return concise guidance for the current app state."""
    safe_error = _safe_detail(last_error or info.error)

    if info.auth is AuthState.SIGNED_OUT or _looks_like_auth_error(safe_error):
        return HelpItem(
            title="Proton VPN login required",
            message=(
                "Proton VPN appears to be installed, but the CLI reports that you are "
                "not logged in."
            ),
            actions=(f"Run: {PROTONVPN_BIN} signin <email>", "Then refresh VPNPilot."),
            detail=safe_error,
        )

    if not has_presets:
        return HelpItem(
            title="No VPN presets configured",
            message="VPNPilot does not have a saved connection target to use from the tray.",
            actions=("Open VPNPilot and create a preset.",),
            detail=safe_error,
        )

    if info.state is ConnState.CLI_MISSING:
        return HelpItem(
            title="Proton VPN CLI not found",
            message=f"VPNPilot could not find {PROTONVPN_BIN} on PATH.",
            actions=(
                f"On Fedora, install Proton VPN CLI with: sudo dnf install {PROTONVPN_RPM_PACKAGE}",
                "Then restart VPNPilot.",
            ),
            detail=safe_error,
        )

    if _looks_like_timeout(safe_error):
        return HelpItem(
            title="Proton VPN command timed out",
            message="The Proton VPN CLI did not respond in time.",
            actions=("Try again.", "Open the log or copy diagnostics if this keeps happening."),
            detail=safe_error,
        )

    if info.state is ConnState.NETWORK_OFFLINE:
        return HelpItem(
            title="Network offline",
            message=(
                "VPNPilot could not find a default network route, so the computer may be offline."
            ),
            actions=("Reconnect to Wi-Fi or Ethernet, then refresh VPNPilot.",),
            detail=safe_error,
        )

    if info.state is ConnState.CAPTIVE_PORTAL:
        return HelpItem(
            title="Captive portal detected",
            message=(
                "NetworkManager reports captive portal connectivity, so Proton VPN cannot "
                "reach the public internet yet."
            ),
            actions=("Open a browser and sign in to the Wi-Fi portal.", "Then refresh VPNPilot."),
            detail=safe_error,
        )

    if info.state is ConnState.NETWORK_LIMITED:
        return HelpItem(
            title="Limited network connectivity",
            message=(
                "NetworkManager reports limited connectivity, so Proton VPN commands may fail "
                "until the network is fully online."
            ),
            actions=("Reconnect to Wi-Fi or Ethernet, then refresh VPNPilot.",),
            detail=safe_error,
        )

    if info.state is ConnState.CLI_ERROR:
        return HelpItem(
            title="Proton VPN CLI error",
            message="The Proton VPN CLI is installed, but a command failed.",
            actions=(
                "Refresh VPNPilot.",
                "Open the log or copy diagnostics if this keeps happening.",
            ),
            detail=safe_error,
        )

    if info.state is ConnState.UNKNOWN:
        return HelpItem(
            title="VPNPilot status unknown",
            message="VPNPilot could not determine the current VPN state.",
            actions=("Refresh VPNPilot.", "Copy diagnostics if the status stays unknown."),
            detail=safe_error,
        )

    if _tool_missing("nmcli", has_nmcli):
        return HelpItem(
            title="NetworkManager tools missing",
            message=(
                "VPNPilot could not find nmcli. Some status and diagnostic details require "
                "NetworkManager tools."
            ),
            actions=("On Fedora, install NetworkManager tools if they are missing.",),
            detail=safe_error,
        )

    if _tool_missing("resolvectl", has_resolvectl):
        return HelpItem(
            title="DNS tools unavailable",
            message="VPNPilot could not find resolvectl, so DNS diagnostics may be incomplete.",
            actions=("On Fedora KDE this is usually provided by systemd-resolved.",),
            detail=safe_error,
        )

    if info.state in {ConnState.CONNECTED, ConnState.DISCONNECTED}:
        return HelpItem(
            title="VPNPilot looks ready",
            message="No setup problems were detected.",
            actions=("Use your tray presets to connect or disconnect.",),
            detail=safe_error,
        )

    return HelpItem(
        title="VPNPilot is working",
        message="VPNPilot is waiting for the current action to finish.",
        actions=("Wait a moment, then refresh if the status does not change.",),
        detail=safe_error,
    )


def _safe_detail(text: str | None) -> str | None:
    if not text:
        return None
    redacted = redact(text).strip()
    if not redacted:
        return None
    return redacted[:300]


def _looks_like_auth_error(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.casefold()
    return (
        "authentication required" in lowered or "sign in" in lowered or "login required" in lowered
    )


def _looks_like_timeout(text: str | None) -> bool:
    return bool(text and "timed out" in text.casefold())


def _tool_missing(name: str, override: bool | None) -> bool:
    if override is not None:
        return not override
    return shutil.which(name) is None
