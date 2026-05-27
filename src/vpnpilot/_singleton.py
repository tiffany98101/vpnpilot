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
import stat
from pathlib import Path

log = logging.getLogger(__name__)


def _is_private_runtime_dir(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and stat.S_IMODE(st.st_mode) == 0o700
    )


def _runtime_dir() -> Path | None:
    raw = os.environ.get("XDG_RUNTIME_DIR", "")
    if raw:
        path = Path(raw)
        if _is_private_runtime_dir(path):
            return path
        log.warning("singleton: unusable XDG_RUNTIME_DIR: %s", path)

    run_user = Path("/run/user") / str(os.getuid())
    if _is_private_runtime_dir(run_user):
        return run_user

    tmp_user = Path("/tmp") / f"vpnpilot-{os.getuid()}"
    try:
        tmp_user.mkdir(mode=0o700, exist_ok=True)
        os.chmod(tmp_user, 0o700)
    except OSError as e:
        log.warning("singleton: cannot create private runtime dir %s (%s)", tmp_user, e)
        return None
    if not _is_private_runtime_dir(tmp_user):
        log.warning("singleton: refusing unsafe runtime dir: %s", tmp_user)
        return None
    return tmp_user


class SingletonLock:
    """Process-lifetime exclusive lock.

    Construct, call `acquire()`. If it returns True you are the
    instance; keep the object alive (the lock is bound to its open
    file descriptor). If it returns False, another instance is
    running and you should exit.
    """

    def __init__(self, name: str = "vpnpilot.lock", *, dir: Path | None = None) -> None:
        base = Path(dir) if dir is not None else _runtime_dir()
        self._path = (base / name) if base is not None else None
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("singleton runtime directory is unavailable")
        return self._path

    def acquire(self) -> bool:
        if self._path is None:
            return False
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._fd = os.open(self._path, flags, 0o600)
        except OSError as e:
            log.warning("singleton: cannot open %s (%s); refusing launch", self._path, e)
            return False
        try:
            st = os.fstat(self._fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                os.close(self._fd)
                self._fd = None
                log.warning("singleton: refusing unsafe lock file: %s", self._path)
                return False
            os.fchmod(self._fd, 0o600)
        except OSError as e:
            os.close(self._fd)
            self._fd = None
            log.warning("singleton: cannot verify %s (%s); refusing launch", self._path, e)
            return False
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
        if self._path is None:
            return None
        try:
            raw = self._path.read_text().strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None
