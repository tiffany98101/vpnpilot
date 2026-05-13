"""Pluggable detection of ProtonVPN connection state.

The default composite reads the network interface list (primary) and
falls back to the CLI status parser (secondary).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .cli import ProtonCLI, parse_status
from .state import ConnectionInfo, ConnState

PROTON_IFACE_RE = re.compile(r"^proton\d+$")


@dataclass(frozen=True)
class IfaceObservation:
    name: str | None  # the matched proton interface, if any
    up: bool          # whether it's flagged UP


class Detector(ABC):
    """Anything that can answer 'are we connected right now?'."""

    @abstractmethod
    async def detect(self) -> ConnectionInfo: ...


# -------- 1) Interface detector (primary) ----------------------------

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
            # Linux IFF_UP = 0x1. Some virtual interfaces report UP via the
            # `operstate` file as "unknown" while being functionally up;
            # POINTOPOINT wireguard interfaces specifically are observed as
            # "unknown". Trust the flags byte.
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
            # format: "11: proton0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu ..."
            try:
                _, name, flagsblob, *_ = line.split(maxsplit=3)
            except ValueError:
                continue
            name = name.rstrip(":")
            if not PROTON_IFACE_RE.match(name):
                continue
            return IfaceObservation(name=name, up="UP" in flagsblob)
        return IfaceObservation(name=None, up=False)


# -------- 2) CLI status detector (secondary) -------------------------

class CLIStatusDetector(Detector):
    def __init__(self, cli: ProtonCLI) -> None:
        self._cli = cli

    async def detect(self) -> ConnectionInfo:
        result = await self._cli.status()
        if not result.ok:
            return ConnectionInfo(state=ConnState.DISCONNECTED, error=result.stderr.strip() or None)
        return parse_status(result.stdout)


# -------- 3) Composite (default) -------------------------------------

class CompositeDetector(Detector):
    """Use the interface signal as truth; enrich with CLI status data.

    - If the interface check says CONNECTED, we run a status query in
      parallel to fill in server/city/country/protocol/load.
    - If the interface check says DISCONNECTED, we trust it. (A
      lingering CLI status saying "connected" while the interface is
      gone is a bug in the CLI's state cache, not a connected state.)
    """

    def __init__(self, primary: Detector, secondary: Detector) -> None:
        self._primary = primary
        self._secondary = secondary

    async def detect(self) -> ConnectionInfo:
        primary = await self._primary.detect()
        if primary.state is not ConnState.CONNECTED:
            return primary
        # Enrich with CLI metadata; do not let CLI failure flip us to disconnected.
        secondary = await self._secondary.detect()
        if secondary.state is ConnState.CONNECTED:
            return ConnectionInfo(
                state=ConnState.CONNECTED,
                interface=primary.interface,
                server=secondary.server,
                city=secondary.city,
                country=secondary.country,
                protocol=secondary.protocol,
                load_percent=secondary.load_percent,
            )
        return primary


def default_detector(cli: ProtonCLI) -> Detector:
    return CompositeDetector(InterfaceDetector(), CLIStatusDetector(cli))
