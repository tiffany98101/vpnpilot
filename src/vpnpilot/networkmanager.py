"""NetworkManager OpenVPN backend helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from dataclasses import dataclass

from .cli import CLIResult
from .state import AuthState, ConnectionInfo, ConnState

log = logging.getLogger(__name__)

NM_BACKEND_NAME = "networkmanager-openvpn"
VPN_TYPES = frozenset({"vpn", "wireguard"})
_PROTON_PROFILE_RE = re.compile(r"(^|[.\s_-])proton(?:vpn)?([.\s_-]|$)|proton\s+vpn", re.IGNORECASE)
_DEFAULT_ROUTE_RE = re.compile(r"^default\b.*\bdev\s+(?P<dev>\S+)")
_RESOLVECTL_FIELD_PREFIXES = (
    "Current Scopes:",
    "DefaultRoute setting:",
    "Default Route:",
    "DNS Domain:",
    "DNS Domains:",
    "DNSSEC NTA:",
    "DNSSEC supported:",
    "DNSSEC setting:",
    "DNSSEC:",
    "DNSOverTLS setting:",
    "LLMNR setting:",
    "MulticastDNS setting:",
    "Protocols:",
)


@dataclass(frozen=True)
class NMConnection:
    name: str
    uuid: str
    type: str
    device: str | None = None

    @property
    def is_vpn(self) -> bool:
        return self.type.lower() in VPN_TYPES

    @property
    def is_proton_like(self) -> bool:
        return looks_like_proton_profile(self.name)


def looks_like_proton_profile(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered.endswith(".protonvpn.tcp")
        or lowered.endswith(".protonvpn.udp")
        or lowered == "protonvpn"
        or lowered == "proton vpn"
        or bool(_PROTON_PROFILE_RE.search(name))
    )


def is_owned_imported_profile_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered.endswith(".protonvpn.tcp") or lowered.endswith(".protonvpn.udp")


def parse_nmcli_connections(text: str, *, active: bool) -> list[NMConnection]:
    connections: list[NMConnection] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = _split_nmcli_terse(raw.rstrip("\n"))
        if active:
            if len(parts) < 4:
                continue
            name, uuid, type_, device = parts[:4]
            connections.append(
                NMConnection(name=name, uuid=uuid, type=type_, device=device or None)
            )
        else:
            if len(parts) < 3:
                continue
            name, uuid, type_ = parts[:3]
            connections.append(NMConnection(name=name, uuid=uuid, type=type_))
    return connections


def _split_nmcli_terse(line: str) -> list[str]:
    fields: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\":
            if i + 1 < len(line) and line[i + 1] in {":", "\\"}:
                buf.append(line[i + 1])
                i += 2
                continue
            buf.append(char)
            i += 1
            continue
        if char == ":":
            fields.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    fields.append("".join(buf))
    return fields


def active_vpn(connections: list[NMConnection]) -> NMConnection | None:
    for conn in connections:
        if conn.is_vpn:
            return conn
    return None


def find_profile(connections: list[NMConnection], name: str) -> NMConnection | None:
    for conn in connections:
        if conn.name == name:
            return conn
    return None


def proton_like_profiles(connections: list[NMConnection]) -> list[NMConnection]:
    return [conn for conn in connections if conn.is_vpn and conn.is_proton_like]


def owned_imported_profiles(connections: list[NMConnection]) -> list[NMConnection]:
    return [conn for conn in connections if conn.is_vpn and is_owned_imported_profile_name(conn.name)]


def owned_vpn(
    connections: list[NMConnection],
    *,
    configured_profile: NMConnection | None = None,
) -> NMConnection | None:
    for conn in connections:
        if not conn.is_vpn:
            continue
        if configured_profile is not None and conn.uuid == configured_profile.uuid:
            return conn
        if is_owned_imported_profile_name(conn.name):
            return conn
    return None


def default_route_device(route_output: str) -> str | None:
    for line in route_output.splitlines():
        match = _DEFAULT_ROUTE_RE.search(line.strip())
        if match:
            return match.group("dev")
    return None


def interface_names(ip_br_addr_output: str) -> list[str]:
    names: list[str] = []
    for line in ip_br_addr_output.splitlines():
        parts = line.split()
        if parts:
            names.append(parts[0].split("@", 1)[0])
    return names


def dns_summary(resolvectl_output: str, device: str | None = None) -> str | None:
    lines = resolvectl_output.splitlines()
    if not lines:
        return None
    current_link: str | None = None
    current_name: str | None = None
    servers: list[str] = []
    current_server: str | None = None
    collecting_servers = False
    for raw in lines:
        line = raw.rstrip()
        link = re.match(r"^Link\s+\d+\s+\((?P<name>[^)]+)\)", line.strip())
        if link:
            if current_name == device and (current_server or servers):
                break
            current_link = line.strip()
            current_name = link.group("name")
            servers = []
            current_server = None
            collecting_servers = False
            continue
        if current_name is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Current DNS Server:"):
            current_server = stripped.split(":", 1)[1].strip()
            collecting_servers = False
        elif stripped.startswith("DNS Servers:"):
            servers.extend(stripped.split(":", 1)[1].strip().split())
            collecting_servers = True
        elif collecting_servers and raw[:1].isspace() and not _is_resolvectl_field(stripped):
            servers.extend(stripped.split())
        elif stripped:
            collecting_servers = False

    if device and current_name != device:
        # Fallback for older/system variants where link names are missing from truncation.
        return None
    selected = _dedupe(([current_server] if current_server else []) + servers)
    if selected and current_name:
        return f"{current_name}: {', '.join(selected)}"
    if current_link:
        return current_link
    return None


def _is_resolvectl_field(stripped: str) -> bool:
    return stripped.startswith(_RESOLVECTL_FIELD_PREFIXES)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


class NetworkManagerOpenVPN:
    """Async nmcli wrapper for imported NetworkManager VPN profiles."""

    name = NM_BACKEND_NAME

    def __init__(
        self,
        *,
        profile_name: str = "",
        nmcli_bin: str = "nmcli",
        ip_bin: str = "ip",
        resolvectl_bin: str = "resolvectl",
        timeout: float = 20.0,
    ) -> None:
        self.profile_name = profile_name.strip()
        self._nmcli = nmcli_bin
        self._ip = ip_bin
        self._resolvectl = resolvectl_bin
        self._timeout = timeout

    @staticmethod
    def is_installed(nmcli_bin: str = "nmcli") -> bool:
        return shutil.which(nmcli_bin) is not None

    async def list_profiles(self) -> tuple[list[NMConnection], CLIResult | None]:
        result = await self._run_nmcli("-t", "-f", "NAME,UUID,TYPE", "connection", "show")
        if not result.ok:
            return [], result
        return parse_nmcli_connections(result.stdout, active=False), None

    async def active_connections(self) -> tuple[list[NMConnection], CLIResult | None]:
        result = await self._run_nmcli(
            "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"
        )
        if not result.ok:
            return [], result
        return parse_nmcli_connections(result.stdout, active=True), None

    async def configured_profile_exists(self) -> bool:
        if not self.profile_name:
            return False
        profiles, error = await self.list_profiles()
        return error is None and find_profile(profiles, self.profile_name) is not None

    async def owned_profiles_exist(self) -> bool:
        profiles, error = await self.list_profiles()
        if error is not None:
            return False
        if self.profile_name and find_profile(profiles, self.profile_name) is not None:
            return True
        return bool(owned_imported_profiles(profiles))

    async def detect(self) -> ConnectionInfo:
        active, active_error = await self.active_connections()
        if active_error is not None:
            return _error_info(active_error, "nmcli active connection lookup failed")
        profiles, profile_error = await self.list_profiles()
        if profile_error is not None:
            return _error_info(profile_error, "nmcli profile lookup failed")
        configured = find_profile(profiles, self.profile_name) if self.profile_name else None
        active_conn = owned_vpn(active, configured_profile=configured)
        external_conn = active_vpn(active)
        addr_result = await self._run_command([self._ip, "-br", "addr"], timeout=5.0)
        route_result = await self._run_command([self._ip, "route"], timeout=5.0)
        default_dev = default_route_device(route_result.stdout) if route_result.ok else None
        dns_result = await self._run_command([self._resolvectl, "status"], timeout=5.0)

        if active_conn is not None:
            device = active_conn.device
            if default_dev and default_dev.startswith("tun"):
                device = default_dev
            elif not device and addr_result.ok:
                names = interface_names(addr_result.stdout)
                device = (
                    default_dev
                    if default_dev in names
                    else next(
                        (name for name in names if name.startswith("tun")),
                        None,
                    )
                )
            dns = dns_summary(dns_result.stdout, device) if dns_result.ok else None
            return ConnectionInfo(
                state=ConnState.CONNECTED,
                auth=AuthState.UNKNOWN,
                server=active_conn.name,
                protocol=active_conn.type,
                interface=device,
                backend=NM_BACKEND_NAME,
                active_profile=active_conn.name,
                default_route_device=default_dev,
                dns_summary=dns,
                backend_reason="active NetworkManager VPN profile",
            )

        if external_conn is not None:
            return ConnectionInfo(
                state=ConnState.EXTERNAL_VPN_ACTIVE,
                auth=AuthState.UNKNOWN,
                server=external_conn.name,
                protocol=external_conn.type,
                interface=external_conn.device,
                backend=NM_BACKEND_NAME,
                active_profile=external_conn.name,
                default_route_device=default_dev,
                backend_reason="external NetworkManager VPN active",
            )
        if self.profile_name and find_profile(profiles, self.profile_name) is None:
            return ConnectionInfo(
                state=ConnState.CLI_ERROR,
                auth=AuthState.UNKNOWN,
                backend=NM_BACKEND_NAME,
                error=f"NetworkManager VPN profile not found: {self.profile_name}",
                backend_reason="configured NetworkManager profile missing",
            )
        return ConnectionInfo(
            state=ConnState.DISCONNECTED,
            auth=AuthState.UNKNOWN,
            backend=NM_BACKEND_NAME,
            default_route_device=default_dev,
            backend_reason="NetworkManager backend selected",
        )

    async def connect(self, **_kwargs) -> CLIResult:
        profile = self.profile_name
        if not profile:
            profiles, error = await self.list_profiles()
            if error is not None:
                return error
            proton_profiles = owned_imported_profiles(profiles)
            if not proton_profiles:
                return CLIResult(
                    returncode=2,
                    stdout="",
                    stderr="No configured NetworkManager VPN profile; set networkmanager_profile.",
                )
            profile = proton_profiles[0].name
        return await self._run_nmcli("connection", "up", profile, timeout=self._timeout)

    async def disconnect(self) -> CLIResult:
        profile = self.profile_name
        active, error = await self.active_connections()
        if error is not None:
            return error
        profiles, profile_error = await self.list_profiles()
        if profile_error is not None:
            return profile_error
        configured = find_profile(profiles, profile) if profile else None
        vpn = owned_vpn(active, configured_profile=configured)
        if vpn is None:
            return CLIResult(
                returncode=0,
                stdout="No owned NetworkManager VPN is active.",
                stderr="",
            )
        profile = configured.name if configured is not None else vpn.name
        return await self._run_nmcli("connection", "down", profile, timeout=self._timeout)

    async def _run_nmcli(self, *args: str, timeout: float | None = None) -> CLIResult:
        return await self._run_command([self._nmcli, *args], timeout=timeout or self._timeout)

    async def _run_command(self, argv: list[str], *, timeout: float) -> CLIResult:
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


def _error_info(result: CLIResult, fallback: str) -> ConnectionInfo:
    message = (result.stderr or result.stdout).strip() or fallback
    if result.returncode == 127:
        message = message or "nmcli: not found"
    return ConnectionInfo(
        state=ConnState.CLI_ERROR,
        auth=AuthState.UNKNOWN,
        backend=NM_BACKEND_NAME,
        error=message,
        backend_reason="NetworkManager backend selected",
    )
