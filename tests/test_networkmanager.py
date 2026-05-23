from __future__ import annotations

import pytest

from vpnpilot.backend import AutoBackend, ProtonCLIBackend
from vpnpilot.cli import CLIResult
from vpnpilot.detect import Detector
from vpnpilot.networkmanager import (
    NetworkManagerOpenVPN,
    active_vpn,
    default_route_device,
    dns_summary,
    find_profile,
    interface_names,
    parse_nmcli_connections,
    proton_like_profiles,
)
from vpnpilot.settings import AppSettings
from vpnpilot.state import ConnectionInfo, ConnState

ACTIVE = """\
us-dc-281.protonvpn.tcp:uuid-1:vpn:tun0
br0:uuid-2:bridge:br0
"""

PROFILES = """\
us-dc-281.protonvpn.tcp:uuid-1:vpn
Home:uuid-2:802-3-ethernet
"""


def test_parse_active_vpn_profile_detection():
    conns = parse_nmcli_connections(ACTIVE, active=True)
    vpn = active_vpn(conns)
    assert vpn is not None
    assert vpn.name == "us-dc-281.protonvpn.tcp"
    assert vpn.device == "tun0"


def test_parse_inactive_profile_listing_and_proton_detection():
    profiles = parse_nmcli_connections(PROFILES, active=False)
    assert find_profile(profiles, "us-dc-281.protonvpn.tcp") is not None
    assert [profile.name for profile in proton_like_profiles(profiles)] == [
        "us-dc-281.protonvpn.tcp"
    ]


def test_route_and_dns_summaries():
    assert interface_names("lo UNKNOWN 127.0.0.1/8\n tun0 UNKNOWN 10.98.0.4/16\n") == [
        "lo",
        "tun0",
    ]
    assert default_route_device("default via 10.98.0.1 dev tun0 proto static metric 50\n") == "tun0"
    resolvectl = """\
Global
Link 9 (tun0)
    Current Scopes: DNS
Current DNS Server: 10.98.0.1
       DNS Servers: 10.98.0.1
"""
    assert dns_summary(resolvectl, "tun0") == "tun0: 10.98.0.1"


@pytest.mark.asyncio
async def test_configured_profile_found(monkeypatch):
    nm = NetworkManagerOpenVPN(profile_name="us-dc-281.protonvpn.tcp")

    async def fake_run_nmcli(*args, timeout=None):
        return CLIResult(0, PROFILES, "")

    monkeypatch.setattr(nm, "_run_nmcli", fake_run_nmcli)
    assert await nm.configured_profile_exists() is True


@pytest.mark.asyncio
async def test_configured_profile_missing_status_is_clear(monkeypatch):
    nm = NetworkManagerOpenVPN(profile_name="missing")

    async def fake_active_connections():
        return [], None

    async def fake_list_profiles():
        return parse_nmcli_connections(PROFILES, active=False), None

    async def fake_run_command(argv, *, timeout):
        return CLIResult(0, "", "")

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)
    monkeypatch.setattr(nm, "list_profiles", fake_list_profiles)
    monkeypatch.setattr(nm, "_run_command", fake_run_command)

    info = await nm.detect()
    assert info.state is ConnState.CLI_ERROR
    assert "not found" in (info.error or "")
    assert "missing" in (info.error or "")


@pytest.mark.asyncio
async def test_disconnect_uses_active_vpn_profile_when_unconfigured(monkeypatch):
    nm = NetworkManagerOpenVPN()
    calls = []

    async def fake_active_connections():
        return parse_nmcli_connections(ACTIVE, active=True), None

    async def fake_run_nmcli(*args, timeout=None):
        calls.append(args)
        return CLIResult(0, "", "")

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)
    monkeypatch.setattr(nm, "_run_nmcli", fake_run_nmcli)

    result = await nm.disconnect()
    assert result.ok
    assert calls == [("connection", "down", "us-dc-281.protonvpn.tcp")]


@pytest.mark.asyncio
async def test_status_prefers_tun_default_route_over_nm_parent_device(monkeypatch):
    nm = NetworkManagerOpenVPN()

    async def fake_active_connections():
        return parse_nmcli_connections(ACTIVE, active=True), None

    async def fake_run_command(argv, *, timeout):
        if argv == ["ip", "-br", "addr"]:
            return CLIResult(0, "tun0 UNKNOWN 10.98.0.4/16\nbr0 UP 192.168.1.211/24\n", "")
        if argv == ["ip", "route"]:
            return CLIResult(0, "default via 10.98.0.1 dev tun0 proto static metric 50\n", "")
        if argv == ["resolvectl", "status"]:
            return CLIResult(
                0,
                "Link 9 (tun0)\n"
                "    Current Scopes: DNS\n"
                "Current DNS Server: 10.98.0.1\n"
                "       DNS Servers: 10.98.0.1\n",
                "",
            )
        return CLIResult(0, "", "")

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)
    monkeypatch.setattr(nm, "_run_command", fake_run_command)

    info = await nm.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "tun0"
    assert info.default_route_device == "tun0"
    assert info.dns_summary == "tun0: 10.98.0.1"


@pytest.mark.asyncio
async def test_nmcli_nonzero_error_is_clear(monkeypatch):
    nm = NetworkManagerOpenVPN()

    async def fake_active_connections():
        return [], CLIResult(10, "", "NetworkManager is not running")

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)

    info = await nm.detect()
    assert info.state is ConnState.CLI_ERROR
    assert "NetworkManager is not running" in (info.error or "")


class FakeCLI:
    def __init__(self):
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self, **kwargs):
        self.connect_calls += 1
        return CLIResult(0, "", "")

    async def disconnect(self):
        self.disconnect_calls += 1
        return CLIResult(0, "", "")


class FakeDetector(Detector):
    async def detect(self) -> ConnectionInfo:
        return ConnectionInfo(state=ConnState.DISCONNECTED)


@pytest.mark.asyncio
async def test_auto_backend_prefers_active_nm_vpn(monkeypatch):
    nm = NetworkManagerOpenVPN()
    proton_cli = FakeCLI()
    backend = AutoBackend(
        ProtonCLIBackend(proton_cli, FakeDetector()),
        nm,
        AppSettings(backend="auto"),
    )

    async def fake_active_connections():
        return parse_nmcli_connections(ACTIVE, active=True), None

    async def fake_detect():
        return ConnectionInfo(
            state=ConnState.CONNECTED,
            backend="networkmanager-openvpn",
            active_profile="us-dc-281.protonvpn.tcp",
        )

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)
    monkeypatch.setattr(nm, "detect", fake_detect)

    info = await backend.detect()
    assert info.backend == "networkmanager-openvpn"
    assert info.active_profile == "us-dc-281.protonvpn.tcp"


@pytest.mark.asyncio
async def test_auto_backend_falls_back_to_proton_cli_without_nm_profile(monkeypatch):
    nm = NetworkManagerOpenVPN()
    proton_cli = FakeCLI()
    backend = AutoBackend(
        ProtonCLIBackend(proton_cli, FakeDetector()),
        nm,
        AppSettings(backend="auto"),
    )

    async def fake_active_connections():
        return [], None

    monkeypatch.setattr(nm, "active_connections", fake_active_connections)

    result = await backend.connect(country="US")
    assert result.ok
    assert proton_cli.connect_calls == 1
