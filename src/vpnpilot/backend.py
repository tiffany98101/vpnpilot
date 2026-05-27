"""Connection backend selection."""

from __future__ import annotations

from typing import Protocol

from .cli import CLIResult, ProtonCLI
from .detect import Detector
from .networkmanager import NM_BACKEND_NAME, NetworkManagerOpenVPN
from .settings import (
    BACKEND_AUTO,
    BACKEND_NETWORKMANAGER_OPENVPN,
    BACKEND_PROTON_CLI,
    AppSettings,
)
from .state import ConnectionInfo


class VPNBackend(Protocol):
    name: str

    async def detect(self) -> ConnectionInfo: ...
    async def connect(self, **kwargs) -> CLIResult: ...
    async def disconnect(self) -> CLIResult: ...


class ProtonCLIBackend:
    name = BACKEND_PROTON_CLI

    def __init__(self, cli: ProtonCLI, detector: Detector) -> None:
        self._cli = cli
        self._detector = detector

    async def detect(self) -> ConnectionInfo:
        info = await self._detector.detect()
        return _with_backend(info, self.name, "Proton CLI backend selected")

    async def connect(self, **kwargs) -> CLIResult:
        return await self._cli.connect(**kwargs)

    async def disconnect(self) -> CLIResult:
        return await self._cli.disconnect()


class AutoBackend:
    name = BACKEND_AUTO

    def __init__(
        self,
        proton: ProtonCLIBackend,
        networkmanager: NetworkManagerOpenVPN,
        settings: AppSettings,
    ) -> None:
        self._proton = proton
        self._nm = networkmanager
        self._settings = settings

    async def detect(self) -> ConnectionInfo:
        selected = await self._selected_for_status()
        info = await selected.detect()
        if selected is self._nm:
            reason = info.backend_reason or "auto selected NetworkManager"
            return _with_backend(info, NM_BACKEND_NAME, f"auto: {reason}")
        return _with_backend(info, BACKEND_PROTON_CLI, "auto: no NetworkManager VPN selected")

    async def connect(self, **kwargs) -> CLIResult:
        selected = await self._selected_for_command()
        return await selected.connect(**kwargs)

    async def disconnect(self) -> CLIResult:
        selected = await self._selected_for_status()
        return await selected.disconnect()

    async def _selected_for_status(self) -> VPNBackend:
        if await self._nm.owned_profiles_exist():
            return self._nm
        if self._nm.profile_name and await self._nm.configured_profile_exists():
            return self._nm
        return self._proton

    async def _selected_for_command(self) -> VPNBackend:
        if self._nm.profile_name and await self._nm.configured_profile_exists():
            return self._nm
        if await self._nm.owned_profiles_exist():
            return self._nm
        return self._proton


def build_backend(cli: ProtonCLI, detector: Detector, settings: AppSettings) -> VPNBackend:
    proton = ProtonCLIBackend(cli, detector)
    nm = NetworkManagerOpenVPN(
        profile_name=settings.networkmanager_profile,
        timeout=settings.nmcli_timeout_seconds,
    )
    if settings.backend == BACKEND_PROTON_CLI:
        return proton
    if settings.backend == BACKEND_NETWORKMANAGER_OPENVPN:
        return nm
    return AutoBackend(proton, nm, settings)


def _with_backend(info: ConnectionInfo, backend: str, reason: str) -> ConnectionInfo:
    return ConnectionInfo(
        state=info.state,
        auth=info.auth,
        account_email=info.account_email,
        server=info.server,
        city=info.city,
        country=info.country,
        protocol=info.protocol,
        load_percent=info.load_percent,
        public_ip=info.public_ip,
        interface=info.interface,
        backend=backend,
        backend_reason=info.backend_reason or reason,
        active_profile=info.active_profile,
        default_route_device=info.default_route_device,
        dns_summary=info.dns_summary,
        error=info.error,
    )
