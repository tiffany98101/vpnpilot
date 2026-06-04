"""Pluggable detection of ProtonVPN connection and auth state.

The default composite reads three signals concurrently:
  * the network interface list (primary truth for connection)
  * `protonvpn status` output (enrichment when connected)
  * `protonvpn info` output (auth state)

Connection and auth are reported as orthogonal axes on ConnectionInfo.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .cli import CLIResult, ProtonCLI, parse_info, parse_status
from .state import AuthState, ConnectionInfo, ConnState

PROTON_IFACE_RE = re.compile(r"^proton\d+$")
_OFFICIAL_GUI_EXECUTABLES = frozenset(
    {
        "protonvpn-app",
        "proton-vpn-gtk-app",
    }
)
_OFFICIAL_GUI_MODULE_PREFIX = "proton.vpn.app.gtk"


@dataclass(frozen=True)
class IfaceObservation:
    name: str | None  # the matched proton interface, if any
    up: bool  # whether it's flagged UP


class Detector(ABC):
    """Anything that can answer 'are we connected right now?'."""

    @abstractmethod
    async def detect(self) -> ConnectionInfo: ...


def detect_official_proton_gui_processes(
    *, proc_root: str = "/proc", exclude_pid: int | None = None
) -> list[int]:
    """Return PIDs that look like the official Proton VPN Linux GUI app.

    Conservative heuristics:
      - executable/comm is exactly `protonvpn-app` (official entry point),
        or
      - Python `-m proton.vpn.app.gtk...` module execution.
    """
    excluded = os.getpid() if exclude_pid is None else exclude_pid
    matches: list[int] = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return matches

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == excluded:
            continue
        cmdline = _read_proc_cmdline(Path(proc_root), pid)
        comm = _read_proc_comm(Path(proc_root), pid)
        if _looks_like_official_proton_gui(cmdline=cmdline, comm=comm):
            matches.append(pid)
    return sorted(matches)


def _read_proc_cmdline(proc_root: Path, pid: int) -> list[str]:
    path = proc_root / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if not raw:
        return []
    return [chunk for chunk in raw.decode("utf-8", errors="replace").split("\x00") if chunk]


def _read_proc_comm(proc_root: Path, pid: int) -> str:
    path = proc_root / str(pid) / "comm"
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _looks_like_official_proton_gui(*, cmdline: list[str], comm: str) -> bool:
    if comm in _OFFICIAL_GUI_EXECUTABLES:
        return True
    if not cmdline:
        return False
    exe = os.path.basename(cmdline[0])
    if exe in _OFFICIAL_GUI_EXECUTABLES:
        return True

    # Covers development/packaging launches through `python -m ...`.
    for i, token in enumerate(cmdline[:-1]):
        if token == "-m" and cmdline[i + 1].startswith(_OFFICIAL_GUI_MODULE_PREFIX):
            return True
    return False


# -------- 1) Interface detector (primary connection signal) -----------


class InterfaceDetector(Detector):
    """Look at the kernel's network interfaces.

    Connected ⇔ at least one interface named matching `^proton\\d+$`
    exists and is in the UP state. We read /sys/class/net first
    (fast, no subprocess), and only fall back to `ip -o link show` if
    /sys is unreadable.
    """

    def __init__(self, *, sysfs: str = "/sys/class/net", ip_bin: str = "ip") -> None:
        self._sysfs = sysfs
        self._ip = ip_bin

    async def detect(self) -> ConnectionInfo:
        obs = await self._observe()
        if obs.name and obs.up:
            return ConnectionInfo(state=ConnState.CONNECTED, interface=obs.name)
        return ConnectionInfo(state=ConnState.DISCONNECTED)

    async def _observe(self) -> IfaceObservation:
        try:
            names = os.listdir(self._sysfs)
        except OSError:
            return await self._observe_via_ip()
        for name in names:
            if not PROTON_IFACE_RE.match(name):
                continue
            flags = self._read_iface_flags(name)
            if flags is None:
                continue
            if flags & 0x1:
                return IfaceObservation(name=name, up=True)
        return IfaceObservation(name=None, up=False)

    def _read_iface_flags(self, name: str) -> int | None:
        path = os.path.join(self._sysfs, name, "flags")
        try:
            with open(path) as f:
                return int(f.read().strip(), 16)
        except (OSError, ValueError):
            return None

    async def _observe_via_ip(self) -> IfaceObservation:
        result = await _run_command([self._ip, "-o", "link", "show"], timeout=5.0)
        if not result.ok:
            return IfaceObservation(name=None, up=False)
        for line in result.stdout.splitlines():
            try:
                _, name, flagsblob, *_ = line.split(maxsplit=3)
            except ValueError:
                continue
            name = name.rstrip(":")
            if not PROTON_IFACE_RE.match(name):
                continue
            return IfaceObservation(name=name, up="UP" in flagsblob)
        return IfaceObservation(name=None, up=False)


# -------- 2) CLI status detector (secondary, for enrichment) ----------


class CLIStatusDetector(Detector):
    def __init__(self, cli: ProtonCLI) -> None:
        self._cli = cli

    async def detect(self) -> ConnectionInfo:
        result = await self._cli.status()
        if result.returncode == 127:
            return ConnectionInfo(state=ConnState.CLI_MISSING, error=result.stderr.strip() or None)
        if not result.ok:
            return ConnectionInfo(
                state=ConnState.CLI_ERROR,
                error=(result.stderr or result.stdout).strip() or "protonvpn status failed",
            )
        return parse_status(result.stdout)


class NetworkStatusDetector(Detector):
    """Best-effort offline probe via the system routing table."""

    def __init__(self, *, ip_bin: str = "ip", nmcli_bin: str = "nmcli") -> None:
        self._ip = ip_bin
        self._nmcli = nmcli_bin

    async def detect(self) -> ConnectionInfo:
        connectivity = await self._nm_connectivity()
        if connectivity == "portal":
            return ConnectionInfo(
                state=ConnState.CAPTIVE_PORTAL,
                error="NetworkManager reports captive portal connectivity",
            )
        if connectivity == "limited":
            return ConnectionInfo(
                state=ConnState.NETWORK_LIMITED,
                error="NetworkManager reports limited connectivity",
            )
        if connectivity == "none":
            return ConnectionInfo(state=ConnState.NETWORK_OFFLINE)

        result = await _run_command([self._ip, "route"], timeout=3.0)
        if result.returncode == 127:
            return ConnectionInfo(state=ConnState.UNKNOWN, error=result.stderr)
        if not result.ok:
            return ConnectionInfo(
                state=ConnState.UNKNOWN,
                error=(result.stderr or result.stdout).strip() or "ip route failed",
            )
        if not self._has_default_route(result.stdout):
            v6_result = await _run_command([self._ip, "-6", "route"], timeout=3.0)
            if v6_result.returncode == 127:
                return ConnectionInfo(state=ConnState.UNKNOWN, error=v6_result.stderr)
            if not v6_result.ok:
                return ConnectionInfo(
                    state=ConnState.UNKNOWN,
                    error=(v6_result.stderr or v6_result.stdout).strip() or "ip -6 route failed",
                )
            if not self._has_default_route(v6_result.stdout):
                return ConnectionInfo(state=ConnState.NETWORK_OFFLINE)
            return ConnectionInfo(state=ConnState.DISCONNECTED)
        return ConnectionInfo(state=ConnState.DISCONNECTED)

    @staticmethod
    def _has_default_route(stdout: str) -> bool:
        return any(
            line.split(maxsplit=1)[0] == "default" for line in stdout.splitlines() if line.strip()
        )

    async def _nm_connectivity(self) -> str | None:
        result = await _run_command(
            [self._nmcli, "-t", "-f", "CONNECTIVITY", "general"],
            timeout=3.0,
        )
        if not result.ok:
            return None
        value = (
            result.stdout.strip().splitlines()[0].strip().casefold()
            if result.stdout.strip()
            else ""
        )
        return value or None


# -------- 3) Auth detector (orthogonal axis) --------------------------


class AuthDetector:
    """Probes auth state via `protonvpn info`.

    Not a Detector subclass — its output is partial (auth + email
    only), and the Composite weaves it into the full ConnectionInfo.
    """

    def __init__(self, cli: ProtonCLI) -> None:
        self._cli = cli

    async def probe(self) -> tuple[AuthState, str | None]:
        result = await self._cli.info()
        if not result.ok:
            return AuthState.UNKNOWN, None
        return parse_info(result.stdout)


# -------- 4) Composite (default) --------------------------------------


class CompositeDetector(Detector):
    """Run interface + auth probes concurrently; enrich via status if connected."""

    def __init__(
        self,
        interface: Detector,
        cli_status: Detector,
        auth: AuthDetector,
        network: Detector | None = None,
    ) -> None:
        self._interface = interface
        self._cli_status = cli_status
        self._auth = auth
        self._network = network or NetworkStatusDetector()

    async def detect(self) -> ConnectionInfo:
        auth_task = asyncio.create_task(self._auth.probe())
        iface_info = await self._interface.detect()
        auth_state, email = await auth_task

        if iface_info.state is not ConnState.CONNECTED:
            status_task = asyncio.create_task(self._cli_status.detect())
            network_info = await self._network.detect()
            status_info = await status_task
            if network_info.state in {
                ConnState.NETWORK_OFFLINE,
                ConnState.CAPTIVE_PORTAL,
                ConnState.NETWORK_LIMITED,
            }:
                return ConnectionInfo(
                    state=network_info.state,
                    auth=auth_state,
                    account_email=email,
                    error=network_info.error,
                )
            if status_info.state in {
                ConnState.TRANSITIONING,
                ConnState.CLI_MISSING,
                ConnState.CLI_ERROR,
                ConnState.UNKNOWN,
            }:
                return ConnectionInfo(
                    state=status_info.state,
                    auth=auth_state,
                    account_email=email,
                    error=status_info.error,
                )
            return ConnectionInfo(
                state=iface_info.state,
                auth=auth_state,
                account_email=email,
                error=iface_info.error,
            )
        # Connected per kernel — enrich via `status`. Treat enrichment
        # failure as non-fatal: we stay CONNECTED with bare-bones info.
        secondary = await self._cli_status.detect()
        if secondary.state is ConnState.CONNECTED:
            return ConnectionInfo(
                state=ConnState.CONNECTED,
                auth=auth_state,
                account_email=email,
                interface=iface_info.interface,
                server=secondary.server,
                city=secondary.city,
                country=secondary.country,
                protocol=secondary.protocol,
                load_percent=secondary.load_percent,
            )
        return ConnectionInfo(
            state=ConnState.CONNECTED,
            auth=auth_state,
            account_email=email,
            interface=iface_info.interface,
        )


def default_detector(cli: ProtonCLI) -> Detector:
    return CompositeDetector(
        InterfaceDetector(),
        CLIStatusDetector(cli),
        AuthDetector(cli),
        NetworkStatusDetector(),
    )


async def _run_command(argv: list[str], *, timeout: float) -> CLIResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CLIResult(returncode=127, stdout="", stderr=f"{argv[0]}: not found")
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        return CLIResult(
            returncode=-1,
            stdout="",
            stderr=f"{' '.join(argv)} timed out after {timeout}s",
            timed_out=True,
        )
    return CLIResult(
        returncode=proc.returncode or 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )
