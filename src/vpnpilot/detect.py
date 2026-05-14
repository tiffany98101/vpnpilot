"""Pluggable detection of ProtonVPN connection and auth state.

The default composite reads three signals concurrently:
  * the network interface list (primary truth for connection)
  * `protonvpn status` output (enrichment when connected)
  * `protonvpn info` output (auth state)

Connection and auth are reported as orthogonal axes on ConnectionInfo.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .cli import ProtonCLI, parse_info, parse_status
from .state import AuthState, ConnectionInfo, ConnState

PROTON_IFACE_RE = re.compile(r"^proton\d+$")


@dataclass(frozen=True)
class IfaceObservation:
    name: str | None  # the matched proton interface, if any
    up: bool          # whether it's flagged UP


class Detector(ABC):
    """Anything that can answer 'are we connected right now?'."""

    @abstractmethod
    async def detect(self) -> ConnectionInfo: ...


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
        obs = await asyncio.to_thread(self._observe)
        if obs.name and obs.up:
            return ConnectionInfo(state=ConnState.CONNECTED, interface=obs.name)
        return ConnectionInfo(state=ConnState.DISCONNECTED)

    def _observe(self) -> IfaceObservation:
        try:
            names = os.listdir(self._sysfs)
        except OSError:
            return self._observe_via_ip()
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

    def _observe_via_ip(self) -> IfaceObservation:
        try:
            cp = subprocess.run(
                [self._ip, "-o", "link", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return IfaceObservation(name=None, up=False)
        if cp.returncode != 0:
            return IfaceObservation(name=None, up=False)
        for line in cp.stdout.splitlines():
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
        if not result.ok:
            return ConnectionInfo(state=ConnState.DISCONNECTED, error=result.stderr.strip() or None)
        return parse_status(result.stdout)


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

    def __init__(self, interface: Detector, cli_status: Detector, auth: AuthDetector) -> None:
        self._interface = interface
        self._cli_status = cli_status
        self._auth = auth

    async def detect(self) -> ConnectionInfo:
        auth_task = asyncio.create_task(self._auth.probe())
        iface_info = await self._interface.detect()
        auth_state, email = await auth_task

        if iface_info.state is not ConnState.CONNECTED:
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
    )
