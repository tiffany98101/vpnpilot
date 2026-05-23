"""Small CLI helpers for backend configuration and diagnostics."""

from __future__ import annotations

import argparse
import asyncio

from .backend import build_backend
from .cli import ProtonCLI
from .detect import default_detector
from .networkmanager import NetworkManagerOpenVPN, proton_like_profiles
from .settings import (
    BACKEND_CHOICES,
    AppSettings,
    SettingsStore,
)


def backend_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vpnpilot backend")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show selected backend status")
    sub.add_parser("list-nm", help="List NetworkManager VPN profiles")
    set_parser = sub.add_parser("set", help="Save backend settings")
    set_parser.add_argument("--backend", choices=sorted(BACKEND_CHOICES))
    set_parser.add_argument("--networkmanager-profile")

    args = parser.parse_args(argv)
    if args.command == "set":
        return _set_backend(args)
    if args.command == "list-nm":
        return asyncio.run(_list_nm())
    return asyncio.run(_status())


def _set_backend(args: argparse.Namespace) -> int:
    store = SettingsStore()
    current = store.load()
    settings = AppSettings(
        backend=args.backend or current.backend,
        networkmanager_profile=(
            args.networkmanager_profile
            if args.networkmanager_profile is not None
            else current.networkmanager_profile
        ),
        prefer_active_nm_vpn=current.prefer_active_nm_vpn,
        nmcli_timeout_seconds=current.nmcli_timeout_seconds,
    )
    store.save(settings)
    print(f"settings: {store.path}")
    print(f"backend: {settings.backend}")
    print(f"networkmanager_profile: {settings.networkmanager_profile or '(unset)'}")
    return 0


async def _status() -> int:
    settings = SettingsStore().load()
    cli = ProtonCLI()
    backend = build_backend(cli, default_detector(cli), settings)
    info = await backend.detect()
    print(f"configured_backend: {settings.backend}")
    print(f"networkmanager_profile: {settings.networkmanager_profile or '(unset)'}")
    print(f"effective_backend: {info.backend or backend.name}")
    if info.backend_reason:
        print(f"backend_reason: {info.backend_reason}")
    print(f"state: {info.state.value}")
    if info.active_profile:
        print(f"active_profile: {info.active_profile}")
    if info.interface:
        print(f"device: {info.interface}")
    if info.default_route_device:
        print(f"default_route_device: {info.default_route_device}")
    if info.dns_summary:
        print(f"dns: {info.dns_summary}")
    if info.error:
        print(f"error: {info.error}")
        return 1
    return 0


async def _list_nm() -> int:
    settings = SettingsStore().load()
    nm = NetworkManagerOpenVPN(
        profile_name=settings.networkmanager_profile,
        timeout=settings.nmcli_timeout_seconds,
    )
    profiles, error = await nm.list_profiles()
    if error is not None:
        print((error.stderr or error.stdout).strip() or f"nmcli exited {error.returncode}")
        return 1
    proton_names = {profile.name for profile in proton_like_profiles(profiles)}
    for profile in profiles:
        if not profile.is_vpn:
            continue
        marker = " proton-like" if profile.name in proton_names else ""
        print(f"{profile.name}\t{profile.type}{marker}")
    return 0
