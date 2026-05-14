"""Parser tests against the exact strings captured in docs/cli-reference.md."""

from __future__ import annotations

from vpnpilot.cli import (
    CLIResult,
    is_auth_error,
    parse_connect_output,
    parse_info,
    parse_status,
)
from vpnpilot.state import AuthState, ConnState

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


# ---- parse_info (auth state probe) ------------------------------------

INFO_SIGNED_IN = "Account: 'tiffany.vonarnim@gmail.com'\n"
INFO_SIGNED_OUT = "Account: 'None'\n"


def test_parse_info_signed_in_returns_email():
    auth, email = parse_info(INFO_SIGNED_IN)
    assert auth is AuthState.SIGNED_IN
    assert email == "tiffany.vonarnim@gmail.com"


def test_parse_info_signed_out_detects_literal_None():
    auth, email = parse_info(INFO_SIGNED_OUT)
    assert auth is AuthState.SIGNED_OUT
    assert email is None


def test_parse_info_garbage_returns_unknown():
    auth, email = parse_info("totally unrelated\n")
    assert auth is AuthState.UNKNOWN
    assert email is None


# ---- is_auth_error (per-command auth-failure detection) ---------------

# Exact strings captured from `protonvpn` 1.0.1 stderr while signed out.
CONNECT_AUTH_ERR = (
    "Error: Authentication required.Please sign in with 'protonvpn signin' before connecting.\n"
    "\nTry 'protonvpn connect --help' for more information.\n"
)
COUNTRIES_AUTH_ERR = (
    "Error: Authentication required to view complete country list. "
    "Please sign in with 'protonvpn signin'\n"
)
CONFIG_AUTH_ERR = (
    "Error: Authentication required to view feature status. "
    "Please sign in with 'protonvpn signin'\n"
)


def test_is_auth_error_for_connect():
    r = CLIResult(returncode=2, stdout="", stderr=CONNECT_AUTH_ERR)
    assert is_auth_error(r) is True


def test_is_auth_error_for_countries_list():
    r = CLIResult(returncode=2, stdout="", stderr=COUNTRIES_AUTH_ERR)
    assert is_auth_error(r) is True


def test_is_auth_error_for_config_list():
    r = CLIResult(returncode=2, stdout="", stderr=CONFIG_AUTH_ERR)
    assert is_auth_error(r) is True


def test_is_auth_error_false_on_success():
    r = CLIResult(returncode=0, stdout="ok", stderr="")
    assert is_auth_error(r) is False


def test_is_auth_error_false_on_unrelated_error():
    r = CLIResult(returncode=2, stdout="", stderr="Network unreachable")
    assert is_auth_error(r) is False
