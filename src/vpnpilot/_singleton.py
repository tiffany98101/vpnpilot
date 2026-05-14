"""Single-instance lock so two tray icons can't appear at once.

Uses an `fcntl.flock` on a file under `$XDG_RUNTIME_DIR` (typically
`/run/user/<uid>/`). The kernel releases the lock automatically when
the holding process exits — including on crash, SIGKILL, or panic —
so there is no stale-lock cleanup to do.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _runtime_dir() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR", "")
    if raw and Path(raw).is_dir():
        return Path(raw)
    # /tmp survives logouts which is undesirable, but it's better than
    # refusing to launch on an exotic environment.
    return Path("/tmp")


class SingletonLock:
    """Process-lifetime exclusive lock.

    Construct, call `acquire()`. If it returns True you are the
    instance; keep the object alive (the lock is bound to its open
    file descriptor). If it returns False, another instance is
    running and you should exit.
    """

    def __init__(self, name: str = "vpnpilot.lock", *, dir: Path | None = None) -> None:
        base = dir if dir is not None else _runtime_dir()
        self._path = Path(base) / name
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> bool:
        try:
            self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            # Failing to even open the lock file is unusual (permissions,
            # readonly fs). Fail open — running twice is worse than not
            # running at all.
            log.warning("singleton: cannot open %s (%s); allowing launch", self._path, e)
            return True
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            return False
        try:
            os.ftruncate(self._fd, 0)
            os.write(self._fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass  # PID note is best-effort; the lock itself is what matters.
        return True

    def held_by_pid(self) -> int | None:
        """Best-effort read of the lock file for a 'pid X is holding it' diagnostic."""
        try:
            raw = self._path.read_text().strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None
