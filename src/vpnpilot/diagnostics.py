"""Diagnostic collection and redaction."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .cli import PROTONVPN_BIN, CLIResult
from .paths import default_log_path

log = logging.getLogger(__name__)

_SENSITIVE_LINE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|auth|bearer|api[_-]?key|private[_-]?key|secret)\b"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([a-z0-9_.-]*(?:token|password|passwd|pwd|api[_-]?key|secret|private[_-]?key)"
    r"[a-z0-9_.-]*\s*[:=]\s*)\S+"
)
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")


@dataclass(frozen=True)
class CommandSpec:
    label: str
    argv: list[str]
    timeout: float = 5.0


async def collect_diagnostics(*, last_error: str | None = None) -> str:
    """Collect a compact diagnostic report suitable for clipboard sharing."""
    log.info("collecting diagnostics")
    sections = [
        ("timestamp", datetime.now(UTC).isoformat()),
        ("vpnpilot_version", __version__),
        ("python", sys.version.replace("\n", " ")),
        ("platform", platform.platform()),
        ("distro", _distro_info()),
        ("desktop_session", _desktop_session_info()),
        ("log_path", str(default_log_path())),
        ("last_app_error", last_error or "None"),
        ("protonvpn_cli", _cli_presence()),
    ]

    command_specs = _command_specs()
    results = await asyncio.gather(
        *(_run_command(spec.argv, timeout=spec.timeout) for spec in command_specs)
    )

    lines = ["VPNPilot Diagnostic Info", ""]
    for key, value in sections:
        lines.append(f"{key}: {redact(value)}")
    for spec, result in zip(command_specs, results, strict=True):
        lines.extend(["", f"## {spec.label}", _format_result(result)])
    return redact("\n".join(lines).rstrip() + "\n")


def redact(text: str) -> str:
    """Redact obvious secrets while preserving useful command context."""
    redacted_lines: list[str] = []
    for line in text.splitlines():
        if _SENSITIVE_LINE_RE.search(line):
            line = _ASSIGNMENT_RE.sub(r"\1<redacted>", line)
            line = _BEARER_RE.sub("Bearer <redacted>", line)
            if _SENSITIVE_LINE_RE.search(line) and "<redacted>" not in line:
                line = re.sub(r"(?<=: ).+", "<redacted>", line)
        line = _LONG_TOKEN_RE.sub("<redacted>", line)
        redacted_lines.append(line)
    return "\n".join(redacted_lines)


async def _run_command(argv: list[str], *, timeout: float) -> CLIResult:
    if shutil.which(argv[0]) is None:
        log.info("diagnostic command unavailable: %s", argv[0])
        return CLIResult(returncode=127, stdout="", stderr=f"{argv[0]}: not found")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        log.warning("diagnostic command failed to start: %s", " ".join(argv), exc_info=True)
        return CLIResult(returncode=127, stdout="", stderr=str(e))
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        log.warning("diagnostic command timed out: %s", " ".join(argv))
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
    result = CLIResult(
        returncode=proc.returncode or 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )
    if not result.ok:
        log.warning("diagnostic command failed: %s rc=%s", " ".join(argv), result.returncode)
    return result


def _command_specs() -> list[CommandSpec]:
    cli = _first_cli_bin()
    specs: list[CommandSpec] = []
    if cli is not None:
        specs.append(CommandSpec(f"{cli} status", [cli, "status"], timeout=10.0))
    else:
        specs.append(CommandSpec("protonvpn status", [PROTONVPN_BIN, "status"], timeout=10.0))
    specs.extend(
        [
            CommandSpec(
                "NetworkManager active connections",
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "NAME,UUID,TYPE,DEVICE",
                    "connection",
                    "show",
                    "--active",
                ],
            ),
            CommandSpec(
                "NetworkManager VPN profiles",
                ["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
            ),
            CommandSpec("interfaces", ["ip", "-br", "addr"]),
            CommandSpec("default route", ["ip", "route"]),
            CommandSpec("DNS status", ["resolvectl", "status"]),
        ]
    )
    return specs


def _first_cli_bin() -> str | None:
    if shutil.which(PROTONVPN_BIN):
        return PROTONVPN_BIN
    return None


def _cli_presence() -> str:
    path = shutil.which(PROTONVPN_BIN)
    return f"{PROTONVPN_BIN}={'found at ' + path if path else 'not found'}"


def _format_result(result: CLIResult) -> str:
    status = f"returncode={result.returncode}"
    if result.timed_out:
        status += " timed_out=true"
    parts = [status]
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        parts.extend(["stdout:", stdout])
    if stderr:
        parts.extend(["stderr:", stderr])
    return redact("\n".join(parts))


def _desktop_session_info() -> str:
    keys = ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_TYPE")
    return "; ".join(f"{key}={os.environ.get(key, '')}" for key in keys)


def _distro_info() -> str:
    path = Path("/etc/os-release")
    try:
        data = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
        return data.get("PRETTY_NAME") or data.get("NAME") or "unknown"
    except OSError:
        return "unknown"
