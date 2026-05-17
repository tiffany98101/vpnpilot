"""Logging setup for vpnpilot."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import default_log_path


def configure_logging(*, level: int = logging.INFO) -> None:
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
        log_path.parent.mkdir(parents=True, exist_ok=True)
        for handler in list(root.handlers):
            if not getattr(handler, "_vpnpilot_log_handler", False):
                continue
            if Path(getattr(handler, "baseFilename", "")) == log_path:
                return
            root.removeHandler(handler)
            handler.close()
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=512_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler._vpnpilot_log_handler = True  # type: ignore[attr-defined]
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).exception("could not initialize file logging")
