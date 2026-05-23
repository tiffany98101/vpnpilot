"""Glue between the CLI, the detector, and the UI.

The controller owns the canonical state. It emits Qt signals for state
changes so the tray (and any future main window) can subscribe without
knowing about polling or subprocess details.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re

from PyQt6.QtCore import QObject, pyqtSignal

from .backend import ProtonCLIBackend, VPNBackend
from .cli import ProtonCLI
from .detect import Detector
from .networkmanager import NM_BACKEND_NAME
from .preset import PresetStore, preset_to_connect_kwargs
from .state import AuthState, ConnectionInfo, ConnState
from .user_state import NullPersistence, Persistence

_SERVER_ID_RE = re.compile(r"^[A-Z]{2}(-[A-Z]{2,3})?#\d+(-TOR)?$")
DEFAULT_POLL_INTERVAL_KEY = "10m"
DEFAULT_POLL_INTERVAL_SECONDS = 600.0
POLL_INTERVAL_CHOICES: dict[str, float | None] = {
    "manual": None,
    "2m": 120.0,
    "5m": 300.0,
    "10m": DEFAULT_POLL_INTERVAL_SECONDS,
    "30m": 1800.0,
}
POLL_INTERVAL_SECONDS = DEFAULT_POLL_INTERVAL_SECONDS
MIN_REFRESH_INTERVAL_SECONDS = 30.0
_PERSISTED_POLL_INTERVAL = object()

log = logging.getLogger(__name__)


class Controller(QObject):
    state_changed = pyqtSignal(object)  # ConnectionInfo
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        cli: ProtonCLI,
        detector: Detector,
        *,
        backend: VPNBackend | None = None,
        poll_interval: float | None | object = _PERSISTED_POLL_INTERVAL,
        min_refresh_interval: float = MIN_REFRESH_INTERVAL_SECONDS,
        persistence: Persistence | None = None,
        preset_store: PresetStore | None = None,
    ) -> None:
        super().__init__()
        self._cli = cli
        self._detector = detector
        self._backend = backend or ProtonCLIBackend(cli, detector)
        self._min_refresh_interval = min_refresh_interval
        self._persistence = persistence or NullPersistence()
        self._poll_interval_key = DEFAULT_POLL_INTERVAL_KEY
        if poll_interval is _PERSISTED_POLL_INTERVAL:
            self._poll_interval_key = self._load_poll_interval_key()
            self._poll_interval = POLL_INTERVAL_CHOICES[self._poll_interval_key]
        else:
            self._poll_interval = poll_interval
        self._preset_store = preset_store
        self._current = ConnectionInfo(state=ConnState.DISCONNECTED)
        self._in_flight: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._last_refresh_started_at: float | None = None
        self._last_error: str | None = None
        self._stopping = asyncio.Event()

    @property
    def current(self) -> ConnectionInfo:
        return self._current

    @property
    def poll_interval_key(self) -> str:
        return self._poll_interval_key

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(
                self._poll_loop(initial_poll=True), name="vpnpilot-poll"
            )

    def stop(self) -> None:
        self._stopping.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
        if self._refresh_task is not None:
            self._refresh_task.cancel()

    # ----- public actions -----

    def connect_preset(self, preset_id: str) -> None:
        # Gate on auth: connect fails noisily at the CLI when signed out,
        # so prevent the round-trip and emit a clear in-app error instead.
        # Disconnect is intentionally not gated — it works even with no
        # session, and we want it available as a recovery path if our
        # detection is somehow wrong.
        if self._requires_auth_gate() and self._current.auth is AuthState.SIGNED_OUT:
            self._emit_error("Sign in to ProtonVPN first.")
            return
        if self._preset_store is None:
            self._emit_error("No presets configured.")
            return
        preset = self._preset_store.get(preset_id)
        if preset is None:
            self._emit_error("Preset not found.")
            return
        self._spawn(self._do_connect(**preset_to_connect_kwargs(preset)))

    def connect_to_location(self, country_code: str, city: str | None = None) -> None:
        """Connect to a specific country or city.

        Routes through the same _do_connect path as connect_preset.
        Gated on auth state — no-ops with error signal when signed out.
        """
        if self._requires_auth_gate() and self._current.auth is AuthState.SIGNED_OUT:
            self._emit_error("Sign in to ProtonVPN first.")
            return
        if city:
            self._spawn(self._do_connect(city=city))
        else:
            self._spawn(self._do_connect(country=country_code))

    def connect_to_server_id(self, server_id: str) -> None:
        """Connect to a specific server by ID (e.g. US-WA#187, US-GA#29-TOR).

        Normalises the ID to uppercase, validates against the server-ID regex
        from docs/cli-reference.md, and raises ValueError on mismatch so the
        caller can surface a client-side error without a round-trip.
        Gated on auth state — no-ops with error signal when signed out.
        """
        if self._requires_auth_gate() and self._current.auth is AuthState.SIGNED_OUT:
            self._emit_error("Sign in to ProtonVPN first.")
            return
        normalized = server_id.strip().upper()
        if not _SERVER_ID_RE.match(normalized):
            raise ValueError(f"Invalid server ID: {server_id!r}")
        self._spawn(self._do_connect(server_id=normalized))

    def disconnect(self) -> None:
        self._spawn(self._do_disconnect())

    def force_refresh(self) -> None:
        """Schedule an out-of-band detection pass.

        Used by the sign-in panel's Recheck button and 5s auto-poll.
        Schedules on the running asyncio loop; if there is none, no-ops
        (which is what we want during shutdown).
        """
        try:
            asyncio.get_running_loop().create_task(self._refresh_state(force=True))
        except RuntimeError:
            log.debug("force_refresh: no running loop, ignoring")

    def set_poll_interval_key(self, key: str) -> None:
        if key not in POLL_INTERVAL_CHOICES:
            key = DEFAULT_POLL_INTERVAL_KEY
        if key == self._poll_interval_key:
            return
        self._poll_interval_key = key
        self._poll_interval = POLL_INTERVAL_CHOICES[key]
        self._persistence.set_poll_interval_key(key)
        self._reschedule_poll_loop()

    # ----- internals -----

    def _load_poll_interval_key(self) -> str:
        key = self._persistence.poll_interval_key()
        if key in POLL_INTERVAL_CHOICES:
            return key
        return DEFAULT_POLL_INTERVAL_KEY

    def _reschedule_poll_loop(self) -> None:
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        if self._poll_interval is None:
            self._poll_task = None
            return
        self._poll_task = asyncio.create_task(
            self._poll_loop(initial_poll=False), name="vpnpilot-poll"
        )

    def _spawn(self, coro) -> None:
        if self._in_flight and not self._in_flight.done():
            log.info("ignoring action — another command is in flight")
            coro.close()
            return
        self._in_flight = asyncio.create_task(coro)

    def _requires_auth_gate(self) -> bool:
        return self._current.backend != NM_BACKEND_NAME

    async def _do_connect(self, **kwargs) -> None:
        log.info("connect requested: %s", _summarize_connect_kwargs(kwargs))
        self._set(ConnectionInfo(state=ConnState.TRANSITIONING))
        result = await self._backend.connect(**kwargs)
        if not result.ok:
            log.warning("connect failed: rc=%s stderr=%r", result.returncode, result.stderr)
            self._emit_error(_short_err(result))
        # Force an immediate detection pass so we don't wait for the next poll tick.
        await self._refresh_state(force=True)

    async def _do_disconnect(self) -> None:
        log.info("disconnect requested")
        self._set(ConnectionInfo(state=ConnState.TRANSITIONING))
        result = await self._backend.disconnect()
        if not result.ok:
            log.warning("disconnect failed: rc=%s stderr=%r", result.returncode, result.stderr)
            self._emit_error(_short_err(result))
        await self._refresh_state(force=True)

    async def _poll_loop(self, *, initial_poll: bool) -> None:
        try:
            if initial_poll and not self._stopping.is_set():
                await self._poll_once()
            while self._poll_interval is not None and not self._stopping.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                if not self._stopping.is_set():
                    await self._poll_once()
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        # Don't trample a transitioning indicator while a command is in flight.
        # (Command coroutines call _refresh_state directly to bypass this.)
        if self._in_flight and not self._in_flight.done():
            return
        await self._refresh_state()

    async def _refresh_state(self, *, force: bool = False) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            await asyncio.shield(self._refresh_task)
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if (
            not force
            and self._last_refresh_started_at is not None
            and now - self._last_refresh_started_at < self._min_refresh_interval
        ):
            log.debug("refresh skipped: cooldown active")
            return
        self._last_refresh_started_at = now
        self._refresh_task = asyncio.create_task(self._run_refresh_state(), name="vpnpilot-refresh")
        try:
            await asyncio.shield(self._refresh_task)
        finally:
            if self._refresh_task is not None and self._refresh_task.done():
                self._refresh_task = None

    async def _run_refresh_state(self) -> None:
        log.debug("refreshing status")
        try:
            info = await self._backend.detect()
        except Exception:  # noqa: BLE001
            log.exception("detector raised; treating as disconnected")
            self._last_error = "detector error"
            info = ConnectionInfo(state=ConnState.UNKNOWN, error="detector error")
        if info.error:
            self._last_error = info.error
            log.info("status refresh result: %s error=%s", info.state.value, info.error)
        else:
            log.info("status refresh result: %s", info.state.value)
        self._set(info)

    def _set(self, info: ConnectionInfo) -> None:
        if info == self._current:
            return
        self._current = info
        # Cache the email whenever we observe a signed-in state. The
        # persistence layer no-ops on None or unchanged values.
        self._persistence.note_email(info.account_email)
        self.state_changed.emit(info)

    def _emit_error(self, message: str) -> None:
        self._last_error = message
        self.error_occurred.emit(message)


def _short_err(result) -> str:
    blob = (result.stderr or result.stdout or "").strip()
    if not blob:
        return f"command exited {result.returncode}"
    # Pick the first non-empty line; CLI errors are typically one line anyway.
    return blob.splitlines()[0][:200]


def _summarize_connect_kwargs(kwargs: dict) -> str:
    parts = []
    for key in ("country", "city", "server_id"):
        if kwargs.get(key):
            parts.append(f"{key}={kwargs[key]}")
    for key in ("p2p", "secure_core", "tor", "random_server"):
        if kwargs.get(key):
            parts.append(key)
    return ", ".join(parts) or "fastest"
