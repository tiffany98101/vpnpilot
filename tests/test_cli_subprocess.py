"""CLI subprocess wrapper tests with no real protonvpn process."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from vpnpilot.cli import CLIResult, ProtonCLI


class FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int, *, hang: bool = False):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(60)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_run_success():
    cli = ProtonCLI()
    fake = FakeProc(b"Status: Disconnected\n", b"", 0)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake)):
        result = await cli.status()
    assert result.ok
    assert "Disconnected" in result.stdout


@pytest.mark.asyncio
async def test_run_returns_127_when_binary_missing():
    cli = ProtonCLI()
    with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError())):
        result = await cli.status()
    assert result.returncode == 127
    assert "not found" in result.stderr


@pytest.mark.asyncio
async def test_run_times_out_and_kills():
    cli = ProtonCLI(timeout=0.05)
    fake = FakeProc(b"", b"", 0, hang=True)
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake)):
        result = await cli.disconnect()
    assert result.timed_out
    assert fake.killed
    assert not result.ok


@pytest.mark.asyncio
async def test_connect_assembles_flags_correctly():
    cli = ProtonCLI()
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProc(b"Connected to IT#1 in Rome, Italy.\nYour new IP address is 1.2.3.4.\n", b"", 0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await cli.connect(city="Seattle", p2p=True)
    assert result.ok
    # captured["args"] == (bin, "connect", "--city", "Seattle", "--p2p")
    assert captured["args"][1:] == ("connect", "--city", "Seattle", "--p2p")


@pytest.mark.asyncio
async def test_connect_server_id_is_positional_last():
    cli = ProtonCLI()
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProc(b"", b"", 0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await cli.connect(server_id="IT#23")
    assert captured["args"][1:] == ("connect", "IT#23")


def test_cliresult_ok():
    assert CLIResult(0, "", "").ok is True
    assert CLIResult(1, "", "boom").ok is False
    assert CLIResult(0, "", "", timed_out=True).ok is False
