from __future__ import annotations

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
