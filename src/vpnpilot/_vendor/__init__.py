"""Vendored third-party modules.

We bundle dependencies that are not (yet) packaged for Fedora's main
repositories so the RPM has no out-of-repo Requires. The vendored code
is imported only as a fallback when the same module is not available
system-wide — see vpnpilot.app for the import shim.

Licenses:
  qasync — BSD-2-Clause. See qasync/__init__.py header for upstream
           authors and license text.
"""
