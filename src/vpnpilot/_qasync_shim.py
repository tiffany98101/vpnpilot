"""Prefer system-installed qasync; fall back to the vendored copy.

This indirection means the dev venv (which pip-installs qasync) and the
RPM (which has no out-of-repo Requires) both work without changes to
the call sites.
"""

from __future__ import annotations

try:
    import qasync as _qasync  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from vpnpilot._vendor import qasync as _qasync  # type: ignore[no-redef]

QEventLoop = _qasync.QEventLoop
__all__ = ["QEventLoop"]
