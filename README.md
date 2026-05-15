# vpnpilot

An unofficial third-party desktop tray app for managing a ProtonVPN
connection on Linux. Wraps the official `protonvpn` CLI. Not affiliated
with or endorsed by Proton AG.

## Current Features

- System tray UI with connection/auth indicators and connect/disconnect actions
- Modeless main window with:
  - connection status panel
  - editable preset library (default preset + create/edit/delete/set-default)
  - country/city server browser tab backed by CLI catalog data
- Sign-in helper panel that copies `protonvpn signin <email>` and rechecks auth state
- `vpnpilot catalog dump` diagnostic command

## Requirements

- Linux desktop with a working tray (Fedora 44+ tested on KDE Plasma 6 and GNOME 47+)
- Official Proton VPN Linux CLI (`proton-vpn-cli`) installed and signed in
- NetworkManager (required by Proton VPN CLI)
- Proton VPN CLI expects `gnome-keyring`; KDE/KWallet setups can work but are more fragile
- Python 3.12+ with PyQt6
- GNOME users need the AppIndicator extension

Do not run the official Proton VPN GUI app and the Proton VPN CLI at the same time.

## Scope and Security Notes

vpnpilot is a UI wrapper around the official Proton CLI. It does not implement
VPN tunneling, killswitch policy, DNS leak protection, split tunneling, or VPN
security controls on its own.

City targeting is limited by the Proton CLI `--city` behavior. vpnpilot stores
country+city context in presets for clarity, but actual city connects remain
best-effort unless you use an exact server ID.

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
