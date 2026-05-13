# vpnpilot

An unofficial third-party desktop tray app for managing a ProtonVPN
connection on Linux. Wraps the official `protonvpn` CLI. Not affiliated
with or endorsed by Proton AG.

## Status

Vertical slice: tray icon (connected/disconnected/transitioning),
state polling, one-click Seattle connect/disconnect. No main window,
settings UI, or server browser yet.

## Requirements

- Fedora 44+ (KDE Plasma 6 or GNOME 47+; Wayland-first, X11-compatible)
- `proton-vpn-cli` installed and signed in
- Python 3.12+ with PyQt6
- GNOME users need the AppIndicator extension

## Install (RPM)

```sh
make rpm
sudo dnf install ./dist/vpnpilot-*.noarch.rpm
```

## Dev

```sh
make install-dev   # editable install + dev deps into a venv
make run
make test
make lint
```

See `CLAUDE.md` for architecture, the CLI abstraction, the detection
strategy, and packaging notes.
