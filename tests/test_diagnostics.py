from __future__ import annotations

import pytest

from vpnpilot.diagnostics import redact


def test_redact_assignment_style_secrets():
    text = "password=hunter2\napi_key: abcdefghijklmnopqrstuvwxyz123456\n"
    out = redact(text)
    assert "hunter2" not in out
    assert "abcdefghijklmnopqrstuvwxyz123456" not in out
    assert "<redacted>" in out


def test_redact_bearer_tokens_and_long_tokens():
    text = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890\n"
        "session=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
    )
    out = redact(text)
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in out
    assert "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in out
    assert "<redacted>" in out


def test_redact_leaves_normal_network_output():
    text = "default via 192.168.1.1 dev wlp0s20f3 proto dhcp\n"
    assert redact(text) == text.rstrip("\n")


@pytest.mark.parametrize(
    ("text", "forbidden", "expected"),
    [
        (
            "Authorization: Basic dXNlcjpwYXNz",
            "dXNlcjpwYXNz",
            "Authorization: Basic <redacted>",
        ),
        (
            "Authorization: Bearer abc.def.ghi.jkl",
            "abc.def.ghi.jkl",
            "Authorization: Bearer <redacted>",
        ),
        (
            "bare bearer abc.def.ghi.jkl",
            "abc.def.ghi.jkl",
            "bare Bearer <redacted>",
        ),
        (
            "Set-Cookie: sessionid=abc123.def456.ghi789; Path=/",
            "abc123.def456.ghi789",
            "Set-Cookie: sessionid=<redacted>; Path=/",
        ),
        (
            "Cookie: a=one; b=two",
            "one",
            "Cookie: a=<redacted>; b=<redacted>",
        ),
        (
            "access_token abc.def.ghi",
            "abc.def.ghi",
            "access_token <redacted>",
        ),
        (
            "access_token=abc.def.ghi",
            "abc.def.ghi",
            "access_token=<redacted>",
        ),
        (
            '"access_token": "abc.def.ghi"',
            "abc.def.ghi",
            '"access_token": "<redacted>"',
        ),
        (
            "jwt abcd.efgh.ijkl",
            "abcd.efgh.ijkl",
            "jwt <redacted>",
        ),
        (
            "mac aa:bb:cc:dd:ee:ff",
            "aa:bb:cc:dd:ee:ff",
            "mac <redacted>",
        ),
        (
            "public ip 8.8.8.8 private 192.168.1.1",
            "8.8.8.8",
            "public ip <redacted-ip> private 192.168.1.1",
        ),
        (
            "path /home/alice/.config/vpnpilot/state.json",
            "/home/alice/",
            "path /home/<user>/.config/vpnpilot/state.json",
        ),
        (
            "path /Users/alice/Library/Logs/vpnpilot.log",
            "/Users/alice/",
            "path /Users/<user>/Library/Logs/vpnpilot.log",
        ),
    ],
)
def test_redact_common_credential_and_identity_formats(text, forbidden, expected):
    out = redact(text)

    assert out == expected
    assert forbidden not in out
    assert redact(out) == out
