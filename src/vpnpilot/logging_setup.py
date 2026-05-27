"""Logging setup for vpnpilot."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import default_log_path


class PrivateRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that keeps current and rotated logs private."""

    def _chmod_logs(self) -> None:
        paths = [Path(self.baseFilename)]
        paths.extend(Path(self.rotation_filename(f"{self.baseFilename}.{i}")) for i in range(1, self.backupCount + 1))
        for path in paths:
            try:
                if path.exists():
                    path.chmod(0o600)
            except OSError:
                logging.getLogger(__name__).warning("could not chmod log file %s", path)

    def doRollover(self) -> None:  # noqa: N802 - stdlib API
        super().doRollover()
        self._chmod_logs()


def _ensure_private_log_path(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.chmod(0o700)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    log_path.chmod(0o600)


def configure_logging(
    *,
    level: int = logging.INFO,
    max_bytes: int = 512_000,
    backup_count: int = 3,
) -> None:
    """Configure console and file logging without making logging fatal."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    log_path = default_log_path()

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    try:
        _ensure_private_log_path(log_path)
        for handler in list(root.handlers):
            if not getattr(handler, "_vpnpilot_log_handler", False):
                continue
            if Path(getattr(handler, "baseFilename", "")) == log_path:
                return
            root.removeHandler(handler)
            handler.close()
        file_handler = PrivateRotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler._chmod_logs()
        file_handler._vpnpilot_log_handler = True  # type: ignore[attr-defined]
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).exception("could not initialize file logging")
