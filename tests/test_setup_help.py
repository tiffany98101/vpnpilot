from __future__ import annotations

from vpnpilot.setup_help import help_for_status
from vpnpilot.state import AuthState, ConnectionInfo, ConnState


def _help(info: ConnectionInfo, *, last_error: str | None = None):
    return help_for_status(
        info,
        last_error=last_error,
        has_nmcli=True,
        has_resolvectl=True,
    )


def test_help_for_cli_missing():
    item = _help(ConnectionInfo(state=ConnState.CLI_MISSING, error="protonvpn: not found"))

    assert item.title == "Proton VPN CLI not found"
    assert "install Proton VPN CLI" in " ".join(item.actions)


def test_help_for_login_required_from_auth_state():
    item = _help(ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT))

    assert item.title == "Proton VPN login required"
    assert "protonvpn signin <email>" in " ".join(item.actions)


def test_help_for_login_required_from_error_text():
    item = _help(
        ConnectionInfo(state=ConnState.CLI_ERROR),
        last_error="Error: Authentication required. Please sign in.",
    )

    assert item.title == "Proton VPN login required"


def test_help_for_network_offline():
    item = _help(ConnectionInfo(state=ConnState.NETWORK_OFFLINE))

    assert item.title == "Network offline"
    assert "Wi-Fi" in " ".join(item.actions)


def test_help_for_command_timeout():
    item = _help(ConnectionInfo(state=ConnState.CLI_ERROR), last_error="status timed out after 10s")

    assert item.title == "Proton VPN command timed out"


def test_help_for_cli_error():
    item = _help(ConnectionInfo(state=ConnState.CLI_ERROR), last_error="exit 2")

    assert item.title == "Proton VPN CLI error"
    assert "command failed" in item.message


def test_help_for_missing_nmcli():
    item = help_for_status(
        ConnectionInfo(state=ConnState.DISCONNECTED),
        has_nmcli=False,
        has_resolvectl=True,
    )

    assert item.title == "NetworkManager tools missing"


def test_help_for_missing_resolvectl():
    item = help_for_status(
        ConnectionInfo(state=ConnState.DISCONNECTED),
        has_nmcli=True,
        has_resolvectl=False,
    )

    assert item.title == "DNS tools unavailable"


def test_help_for_no_presets():
    item = help_for_status(
        ConnectionInfo(state=ConnState.DISCONNECTED),
        has_presets=False,
        has_nmcli=True,
        has_resolvectl=True,
    )

    assert item.title == "No VPN presets configured"


def test_help_for_unknown_status():
    item = _help(ConnectionInfo(state=ConnState.UNKNOWN), last_error="detector error")

    assert item.title == "VPNPilot status unknown"


def test_help_for_healthy_connected_and_disconnected():
    connected = _help(ConnectionInfo(state=ConnState.CONNECTED))
    disconnected = _help(ConnectionInfo(state=ConnState.DISCONNECTED))

    assert connected.title == "VPNPilot looks ready"
    assert disconnected.title == "VPNPilot looks ready"


def test_help_detail_redacts_secrets():
    item = _help(
        ConnectionInfo(state=ConnState.CLI_ERROR),
        last_error="api_key=abcdefghijklmnopqrstuvwxyz123456 password=hunter2",
    )

    assert item.detail is not None
    assert "abcdefghijklmnopqrstuvwxyz123456" not in item.detail
    assert "hunter2" not in item.detail
    assert "<redacted>" in item.detail
