"""Detection layer tests with a fake sysfs and a fake CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from vpnpilot.cli import CLIResult, ProtonCLI
from vpnpilot.detect import (
    AuthDetector,
    CLIStatusDetector,
    CompositeDetector,
    InterfaceDetector,
)
from vpnpilot.state import AuthState, ConnState


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
    def __init__(
        self,
        status_stdout: str = "",
        status_ok: bool = True,
        info_stdout: str = "Account: 'tiffany.vonarnim@gmail.com'\n",
        info_ok: bool = True,
    ):
        self._status_stdout = status_stdout
        self._status_ok = status_ok
        self._info_stdout = info_stdout
        self._info_ok = info_ok

    async def status(self):  # type: ignore[override]
        return CLIResult(
            returncode=0 if self._status_ok else 1,
            stdout=self._status_stdout,
            stderr="" if self._status_ok else "boom",
        )

    async def info(self):  # type: ignore[override]
        return CLIResult(
            returncode=0 if self._info_ok else 1,
            stdout=self._info_stdout,
            stderr="" if self._info_ok else "boom",
        )


def make_composite(sysfs_path, *, cli=None):
    """Helper: assemble a CompositeDetector with the given parts."""
    cli = cli or FakeCLI()
    return CompositeDetector(
        InterfaceDetector(sysfs=str(sysfs_path)),
        CLIStatusDetector(cli),
        AuthDetector(cli),
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
    cli = FakeCLI(
        status_stdout="Status: Connected\nServer: X#1 in Y, Z\nLoad: 5%\nProtocol: wireguard\n"
    )
    composite = make_composite(sysfs, cli=cli)
    info = await composite.detect()
    assert info.state is ConnState.DISCONNECTED


@pytest.mark.asyncio
async def test_composite_enriches_when_interface_says_connected(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"proton0": 0x10091})
    cli = FakeCLI(
        status_stdout="Status: Connected\nServer: US-WA#1 in Seattle, United States\nLoad: 15%\nProtocol: wireguard\n"
    )
    composite = make_composite(sysfs, cli=cli)
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
    cli = FakeCLI(status_stdout="", status_ok=False)
    composite = make_composite(sysfs, cli=cli)
    info = await composite.detect()
    assert info.state is ConnState.CONNECTED
    assert info.interface == "proton0"
    assert info.server is None  # enrichment failed gracefully


# ---- auth axis -------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_detector_signed_in_returns_email():
    cli = FakeCLI(info_stdout="Account: 'tiffany.vonarnim@gmail.com'\n")
    det = AuthDetector(cli)
    auth, email = await det.probe()
    assert auth is AuthState.SIGNED_IN
    assert email == "tiffany.vonarnim@gmail.com"


@pytest.mark.asyncio
async def test_auth_detector_signed_out_returns_None():
    cli = FakeCLI(info_stdout="Account: 'None'\n")
    det = AuthDetector(cli)
    auth, email = await det.probe()
    assert auth is AuthState.SIGNED_OUT
    assert email is None


@pytest.mark.asyncio
async def test_auth_detector_returns_unknown_on_cli_failure():
    cli = FakeCLI(info_stdout="", info_ok=False)
    det = AuthDetector(cli)
    auth, email = await det.probe()
    assert auth is AuthState.UNKNOWN
    assert email is None


@pytest.mark.asyncio
async def test_composite_carries_signed_out_when_disconnected(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {})
    cli = FakeCLI(info_stdout="Account: 'None'\n")
    composite = make_composite(sysfs, cli=cli)
    info = await composite.detect()
    assert info.state is ConnState.DISCONNECTED
    assert info.auth is AuthState.SIGNED_OUT
    assert info.account_email is None


@pytest.mark.asyncio
async def test_composite_carries_signed_in_email_when_connected(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {"proton0": 0x10091})
    cli = FakeCLI(
        status_stdout="Status: Connected\nServer: US-WA#1 in Seattle, United States\nLoad: 5%\nProtocol: wireguard\n",
        info_stdout="Account: 'tiffany.vonarnim@gmail.com'\n",
    )
    composite = make_composite(sysfs, cli=cli)
    info = await composite.detect()
    assert info.state is ConnState.CONNECTED
    assert info.auth is AuthState.SIGNED_IN
    assert info.account_email == "tiffany.vonarnim@gmail.com"


@pytest.mark.asyncio
async def test_composite_unknown_auth_when_info_fails(tmp_path):
    sysfs = make_fake_sysfs(tmp_path, {})
    cli = FakeCLI(info_ok=False)
    composite = make_composite(sysfs, cli=cli)
    info = await composite.detect()
    assert info.auth is AuthState.UNKNOWN
