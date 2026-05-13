"""Wrapper around the `protonvpn` CLI.

All subprocess calls go through `_run`, which enforces a timeout and
returns a structured result. Output is parsed by small dedicated
functions kept side-by-side with the format strings they target, so a
CLI upgrade only touches the parser.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass

from .state import ConnectionInfo, ConnState

DEFAULT_TIMEOUT = 30.0  # seconds; status can take 10s+ on first call after a state change
PROTONVPN_BIN = "protonvpn"


@dataclass(frozen=True)
class CLIResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class ProtonCLI:
    """Async wrapper. All public methods are coroutines."""

    def __init__(self, bin_path: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._bin = bin_path or PROTONVPN_BIN
        self._timeout = timeout

    @staticmethod
    def is_installed(bin_path: str = PROTONVPN_BIN) -> bool:
        return shutil.which(bin_path) is not None

    async def _run(self, *args: str, timeout: float | None = None) -> CLIResult:
        t = timeout if timeout is not None else self._timeout
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return CLIResult(returncode=127, stdout="", stderr=f"{self._bin}: not found")
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=t)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return CLIResult(returncode=-1, stdout="", stderr=f"timed out after {t}s", timed_out=True)
        return CLIResult(
            returncode=proc.returncode or 0,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )

    async def status(self) -> CLIResult:
        # status can be slow the first time after a state change because the
        # CLI may refresh the server list; allow extra timeout.
        return await self._run("status", timeout=max(self._timeout, 15.0))

    async def disconnect(self) -> CLIResult:
        return await self._run("disconnect")

    async def connect(
        self,
        *,
        city: str | None = None,
        country: str | None = None,
        server_id: str | None = None,
        p2p: bool = False,
        secure_core: bool = False,
        tor: bool = False,
        random_server: bool = False,
    ) -> CLIResult:
        args: list[str] = ["connect"]
        if city:
            args += ["--city", city]
        if country:
            args += ["--country", country]
        if p2p:
            args.append("--p2p")
        if secure_core:
            args.append("--securecore")
        if tor:
            args.append("--tor")
        if random_server:
            args.append("--random")
        if server_id:
            args.append(server_id)
        return await self._run(*args, timeout=max(self._timeout, 30.0))

    async def info(self) -> CLIResult:
        return await self._run("info")


# ---- Parsers (kept next to the format strings they target) -----------

_STATUS_LINE = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE)
_SERVER_LINE = re.compile(
    r"^Server:\s*(?P<server>\S+)\s+in\s+(?P<city>.+?),\s*(?P<country>.+?)\s*$",
    re.MULTILINE,
)
_LOAD_LINE = re.compile(r"^Load:\s*(\d+)%", re.MULTILINE)
_PROTOCOL_LINE = re.compile(r"^Protocol:\s*(\S+)", re.MULTILINE)
_CONNECT_IP_LINE = re.compile(
    r"^Your new IP address is\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+)\.?\s*$",
    re.MULTILINE,
)
_CONNECT_SERVER_LINE = re.compile(
    r"^Connected to\s+(?P<server>\S+)\s+in\s+(?P<city>.+?),\s*(?P<country>.+?)\.\s*$",
    re.MULTILINE,
)


def parse_status(stdout: str) -> ConnectionInfo:
    """Parse `protonvpn status` output. Always returns a ConnectionInfo."""
    m = _STATUS_LINE.search(stdout)
    if not m:
        return ConnectionInfo(state=ConnState.DISCONNECTED, error="unparseable status output")
    raw = m.group(1).strip().lower()
    if raw.startswith("disconnect"):
        return ConnectionInfo(state=ConnState.DISCONNECTED)
    if not raw.startswith("connect"):
        return ConnectionInfo(state=ConnState.DISCONNECTED, error=f"unknown status: {raw}")

    info = {"state": ConnState.CONNECTED}
    if sm := _SERVER_LINE.search(stdout):
        info["server"] = sm.group("server")
        info["city"] = sm.group("city")
        info["country"] = sm.group("country")
    if lm := _LOAD_LINE.search(stdout):
        info["load_percent"] = int(lm.group(1))
    if pm := _PROTOCOL_LINE.search(stdout):
        info["protocol"] = pm.group(1)
    return ConnectionInfo(**info)


def parse_connect_output(stdout: str) -> dict[str, str]:
    """Extract server/city/country/public_ip from a successful `connect`.

    Returns a possibly-partial dict (any key may be missing if the CLI
    didn't print it). Callers should merge this into their state, not
    rely on every key being present.
    """
    out: dict[str, str] = {}
    if sm := _CONNECT_SERVER_LINE.search(stdout):
        out["server"] = sm.group("server")
        out["city"] = sm.group("city")
        out["country"] = sm.group("country")
    if im := _CONNECT_IP_LINE.search(stdout):
        out["public_ip"] = im.group("ip")
    return out


def is_not_signed_in(result: CLIResult) -> bool:
    """Heuristic. The CLI's exit code is non-zero and it suggests `signin`."""
    if result.ok:
        return False
    blob = (result.stdout + "\n" + result.stderr).lower()
    return "signin" in blob or "sign in" in blob or "not signed in" in blob
