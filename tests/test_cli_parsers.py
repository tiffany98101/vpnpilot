"""Parser tests against the exact strings captured in docs/cli-reference.md."""

from __future__ import annotations

from vpnpilot.cli import (
    CLIResult,
    is_not_signed_in,
    parse_connect_output,
    parse_status,
)
from vpnpilot.state import ConnState

STATUS_CONNECTED = """\
Status: Connected
Server: US-WA#232 in Seattle, United States
Load: 32%
Protocol: wireguard
"""

STATUS_DISCONNECTED = "Status: Disconnected\n"

STATUS_WITH_REFRESH_PREAMBLE = (
    "Server list is outdated, updating... This may take a moment.\n"
    + STATUS_CONNECTED
)

CONNECT_SUCCESS = (
    "Connected to US-WA#347 in Seattle, United States. \n"
    "Your new IP address is 159.26.103.107.\n"
)


def test_parse_status_connected():
    info = parse_status(STATUS_CONNECTED)
    assert info.state is ConnState.CONNECTED
    assert info.server == "US-WA#232"
    assert info.city == "Seattle"
    assert info.country == "United States"
    assert info.load_percent == 32
    assert info.protocol == "wireguard"


def test_parse_status_disconnected():
    info = parse_status(STATUS_DISCONNECTED)
    assert info.state is ConnState.DISCONNECTED
    assert info.server is None


def test_parse_status_with_refresh_preamble():
    info = parse_status(STATUS_WITH_REFRESH_PREAMBLE)
    assert info.state is ConnState.CONNECTED
    assert info.server == "US-WA#232"


def test_parse_status_garbage_is_disconnected():
    info = parse_status("totally unrelated output\n")
    assert info.state is ConnState.DISCONNECTED
    assert info.error is not None


def test_parse_connect_output_extracts_all():
    out = parse_connect_output(CONNECT_SUCCESS)
    assert out["server"] == "US-WA#347"
    assert out["city"] == "Seattle"
    assert out["country"] == "United States"
    assert out["public_ip"] == "159.26.103.107"


def test_parse_connect_output_handles_missing_ip():
    text = "Connected to IT#23 in Milan, Italy.\n"
    out = parse_connect_output(text)
    assert out["server"] == "IT#23"
    assert "public_ip" not in out


def test_is_not_signed_in_detects_signin_hint():
    r = CLIResult(returncode=1, stdout="", stderr="Please run `protonvpn signin` first.")
    assert is_not_signed_in(r) is True


def test_is_not_signed_in_returns_false_on_success():
    r = CLIResult(returncode=0, stdout="ok", stderr="")
    assert is_not_signed_in(r) is False


def test_is_not_signed_in_returns_false_on_unrelated_error():
    r = CLIResult(returncode=2, stdout="", stderr="Network unreachable")
    assert is_not_signed_in(r) is False
