"""Detection layer tests with a fake sysfs and a fake CLI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vpnpilot.cli import ProtonCLI
from vpnpilot.detect import (
    CLIStatusDetector,
    CompositeDetector,
    InterfaceDetector,
)
from vpnpilot.state import ConnState


def make_fake_sysfs(tmp_path: Path, interfaces: dict[str, int]) -> Path:
    """Create a tmpdir mimicking /sys/class/net with given interface flag values."""
    sysfs = tmp_path / "sys" / "class" / "net"
    sysfs.mkdir(parents=True)
    for name, flags in interfaces.items():
        (sysfs / name).mkdir()
        (sysfs / name / "flags").write_text(f"{flags:#x}\n")
    return sysfs


@pytest.mark.asyncio
async def test_interface_detector_connected_with_proton0(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"lo": 0x9, "proton0": 0x10091, "ipv6leakintrf0": 0x1003})
    det = InterfaceDetector(sysfs=str(sysfs))
    info = await det.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "proton0"


@pytest.mark.asyncio
async def test_interface_detector_matches_proton1(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"proton1": 0x10091})
    det = InterfaceDetector(sysfs=str(sysfs))
    info = await det.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "proton1"


@pytest.mark.asyncio
async def test_interface_detector_ignores_proton_prefix_without_digit(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"protonmail": 0x10091})
    det = InterfaceDetector(sysfs=str(sysfs))
    info = await det.detect()
    assert info.state is ConnState.DISCONNECTED


@pytest.mark.asyncio
async def test_interface_detector_disconnected_when_iface_absent(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"lo": 0x9, "eth0": 0x1003})
    det = InterfaceDetector(sysfs=str(sysfs))
    info = await det.detect()
    assert info.state is ConnState.DISCONNECTED


@pytest.mark.asyncio
async def test_interface_detector_disconnected_when_iface_down(tmp_path):
    # 0x1002 has IFF_UP (0x1) NOT set. Bit 0 unset means down.
    sysfs = make_fake_sysfs(tmp_path, {"proton0": 0x1002})
    det = InterfaceDetector(sysfs=str(sysfs))
    info = await det.detect()
    assert info.state is ConnState.DISCONNECTED


@pytest.mark.asyncio
async def test_interface_detector_handles_missing_sysfs(tmp_path):
    # Sysfs path absent AND `ip` binary unreachable: fall back gracefully,
    # not crash. Without faking the second, the host's real `ip` would
    # report whatever interfaces are actually present.
    det = InterfaceDetector(sysfs=str(tmp_path / "nope"), ip_bin="/nonexistent/ip")
    info = await det.detect()
    assert info.state is ConnState.DISCONNECTED


class FakeCLI(ProtonCLI):
    def __init__(self, status_stdout: str = "", status_ok: bool = True):
        self._status_stdout = status_stdout
        self._status_ok = status_ok

    async def status(self):  # type: ignore[override]
        from vpnpilot.cli import CLIResult

        return CLIResult(
            returncode=0 if self._status_ok else 1,
            stdout=self._status_stdout,
            stderr="" if self._status_ok else "boom",
        )


@pytest.mark.asyncio
async def test_cli_status_detector_parses_connected():
    cli = FakeCLI(status_stdout="Status: Connected\nServer: US-WA#1 in Seattle, United States\nLoad: 10%\nProtocol: wireguard\n")
    det = CLIStatusDetector(cli)
    info = await det.detect()
    assert info.state is ConnState.CONNECTED
    assert info.server == "US-WA#1"


@pytest.mark.asyncio
async def test_composite_trusts_interface_when_disconnected(tmp_path):
    # Interface absent, but CLI status (stale) says connected.
    sysfs = make_fake_sysfs(tmp_path, {})
    primary = InterfaceDetector(sysfs=str(sysfs))
    secondary = FakeCLI(status_stdout="Status: Connected\nServer: X#1 in Y, Z\nLoad: 5%\nProtocol: wireguard\n")
    composite = CompositeDetector(primary, CLIStatusDetector(secondary))
    info = await composite.detect()
    assert info.state is ConnState.DISCONNECTED


@pytest.mark.asyncio
async def test_composite_enriches_when_interface_says_connected(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"proton0": 0x10091})
    primary = InterfaceDetector(sysfs=str(sysfs))
    secondary = FakeCLI(status_stdout="Status: Connected\nServer: US-WA#1 in Seattle, United States\nLoad: 15%\nProtocol: wireguard\n")
    composite = CompositeDetector(primary, CLIStatusDetector(secondary))
    info = await composite.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "proton0"
    assert info.server == "US-WA#1"
    assert info.city == "Seattle"
    assert info.protocol == "wireguard"
    assert info.load_percent == 15


@pytest.mark.asyncio
async def test_composite_stays_connected_if_cli_fails_but_iface_up(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"proton0": 0x10091})
    primary = InterfaceDetector(sysfs=str(sysfs))
    secondary = FakeCLI(status_stdout="", status_ok=False)
    composite = CompositeDetector(primary, CLIStatusDetector(secondary))
    info = await composite.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "proton0"
    assert info.server is None  # enrichment failed gracefully
