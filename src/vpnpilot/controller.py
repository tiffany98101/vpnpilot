"""Glue between the CLI, the detector, and the UI.

The controller owns the canonical state. It emits Qt signals for state
changes so the tray (and any future main window) can subscribe without
knowing about polling or subprocess details.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from .cli import ProtonCLI
from .detect import Detector
from .state import AuthState, ConnectionInfo, ConnState
from .user_state import NullPersistence, Persistence

log = logging.getLogger(__name__)


class Controller(QObject):
    state_changed = pyqtSignal(object)  # ConnectionInfo
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        cli: ProtonCLI,
        detector: Detector,
        *,
        poll_interval: float = 3.0,
        persistence: Persistence | None = None,
    ) -> None:
        super().__init__()
        self._cli = cli
        self._detector = detector
        self._poll_interval = poll_interval
        self._persistence = persistence or NullPersistence()
        self._current = ConnectionInfo(state=ConnState.DISCONNECTED)
        self._in_flight: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    @property
    def current(self) -> ConnectionInfo:
        return self._current

    def start(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop(), name="vpnpilot-poll")

    def stop(self) -> None:
        self._stopping.set()
        if self._poll_task is not None:
            self._poll_task.cancel()

    # ----- public actions -----

    def connect_preset_seattle(self) -> None:
        # Gate on auth: connect fails noisily at the CLI when signed out,
        # so prevent the round-trip and emit a clear in-app error instead.
        # Disconnect is intentionally not gated — it works even with no
        # session, and we want it available as a recovery path if our
        # detection is somehow wrong.
        if self._current.auth is AuthState.SIGNED_OUT:
            self.error_occurred.emit("Sign in to ProtonVPN first.")
            return
        self._spawn(self._do_connect(city="Seattle"))

    def disconnect(self) -> None:
        self._spawn(self._do_disconnect())

    def force_refresh(self) -> None:
        """Schedule an out-of-band detection pass.

        Used by the sign-in panel's Recheck button and 5s auto-poll.
        Schedules on the running asyncio loop; if there is none, no-ops
        (which is what we want during shutdown).
        """
        try:
            asyncio.get_running_loop().create_task(self._refresh_state())
        except RuntimeError:
            log.debug("force_refresh: no running loop, ignoring")

    # ----- internals -----

    def _spawn(self, coro) -> None:
        if self._in_flight and not self._in_flight.done():
            log.info("ignoring action — another command is in flight")
            coro.close()
            return
        self._in_flight = asyncio.create_task(coro)

    async def _do_connect(self, **kwargs) -> None:
        self._set(ConnectionInfo(state=ConnState.TRANSITIONING))
        result = await self._cli.connect(**kwargs)
        if not result.ok:
            self.error_occurred.emit(_short_err(result))
        # Force an immediate detection pass so we don't wait for the next poll tick.
        await self._refresh_state()

    async def _do_disconnect(self) -> None:
        self._set(ConnectionInfo(state=ConnState.TRANSITIONING))
        result = await self._cli.disconnect()
        if not result.ok:
            self.error_occurred.emit(_short_err(result))
        await self._refresh_state()

    async def _poll_loop(self) -> None:
        try:
            while not self._stopping.is_set():
                await self._poll_once()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        # Don't trample a transitioning indicator while a command is in flight.
        # (Command coroutines call _refresh_state directly to bypass this.)
        if self._in_flight and not self._in_flight.done():
            return
        await self._refresh_state()

    async def _refresh_state(self) -> None:
        try:
            info = await self._detector.detect()
        except Exception:  # noqa: BLE001
            log.exception("detector raised; treating as disconnected")
            info = ConnectionInfo(state=ConnState.DISCONNECTED, error="detector error")
        self._set(info)

    def _set(self, info: ConnectionInfo) -> None:
        if info == self._current:
            return
        self._current = info
        # Cache the email whenever we observe a signed-in state. The
        # persistence layer no-ops on None or unchanged values.
        self._persistence.note_email(info.account_email)
        self.state_changed.emit(info)


def _short_err(result) -> str:
    blob = (result.stderr or result.stdout or "").strip()
    if not blob:
        return f"protonvpn exited {result.returncode}"
    # Pick the first non-empty line; CLI errors are typically one line anyway.
    return blob.splitlines()[0][:200]
