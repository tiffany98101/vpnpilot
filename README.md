# VPN Pilot

VPN Pilot is an unofficial third-party Linux desktop tray app for managing a Proton VPN connection.

It wraps the official `protonvpn` CLI and provides a small desktop UI for connection status, presets, country/city browsing, sign-in state, and common connect/disconnect actions.

Repository: https://github.com/tiffany98101/vpnpilot

> [!IMPORTANT]
> VPN Pilot is not affiliated with, endorsed by, or supported by Proton AG. It does not replace Proton VPN's official Linux client.

## Screenshots

| Connected | Server browser |
| --- | --- |
| ![VPN Pilot connected state](docs/screenshots/connected.png) | ![VPN Pilot country and city browser](docs/screenshots/NewCity.png) |

| Tray menu | Tray status |
| --- | --- |
| ![VPN Pilot tray menu](docs/screenshots/tray.png) | ![VPN Pilot tray status](docs/screenshots/tray2.png) |

## Current Features

- System tray UI with connection and authentication indicators
- Connect and disconnect actions from the tray
- Modeless main window with a connection status panel
- Editable preset library
- Country/city server browser backed by Proton VPN CLI catalog data
- Sign-in helper panel that copies `protonvpn signin <email>` and rechecks auth state
- Single-instance lock to prevent duplicate tray icons
- `vpnpilot catalog dump` diagnostic command

## Requirements

- Linux desktop with a working system tray
- Fedora KDE Plasma tested
- GNOME users need AppIndicator support
- Official Proton VPN Linux CLI installed and signed in
- NetworkManager
- Python 3.12+
- PyQt6

Do not run the official Proton VPN GUI app and the Proton VPN CLI at the same time.

## Install from source

```sh
git clone https://github.com/tiffany98101/vpnpilot.git
cd vpnpilot

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip wheel
pip install -e .
vpnpilot
```

If the script entry point is not on your shell path, run it from the project directory:

```sh
python -m vpnpilot
```

## Developer install

```sh
git clone https://github.com/tiffany98101/vpnpilot.git
cd vpnpilot

make install-dev
make run
```

Useful dev commands:

```sh
make test
make lint
make clean
```

## Build and install the RPM

On Fedora, install the RPM build tools first:

```sh
sudo dnf install -y git make rpm-build python3-devel pyproject-rpm-macros desktop-file-utils
```

Then build and install:

```sh
git clone https://github.com/tiffany98101/vpnpilot.git
cd vpnpilot

make rpm
sudo dnf install ./dist/vpnpilot-*.noarch.rpm
vpnpilot
```

## First-time setup

Install Proton VPN's official Linux CLI first and sign in there before using VPN Pilot.

Check that the CLI is available:

```sh
command -v protonvpn
protonvpn --help
```

Sign in with Proton VPN CLI:

```sh
protonvpn signin you@example.com
```

Check Proton VPN CLI status:

```sh
protonvpn status
```

Then start VPN Pilot:

```sh
vpnpilot
```

## Basic usage

1. Start `vpnpilot`.
2. Use the tray icon to check VPN/auth state.
3. Open the main window for details, presets, and the country/city browser.
4. Create or edit presets for common locations.
5. Use Connect/Disconnect from the tray or main UI.
6. If something fails, run `vpnpilot` from a terminal so you can see diagnostic output.

City targeting depends on Proton CLI `--city` behavior. VPN Pilot stores country and city context in presets for clarity, but city connects are still best-effort unless you use an exact server ID.

## Diagnostics

Dump the Proton VPN CLI catalog data used by VPN Pilot:

```sh
vpnpilot catalog dump
```

Check NetworkManager:

```sh
systemctl status NetworkManager --no-pager
```

Check Proton VPN CLI directly:

```sh
protonvpn status
```

## Scope and security notes

VPN Pilot is a UI wrapper around Proton VPN's official CLI.

It does **not** implement VPN tunneling, WireGuard/OpenVPN handling, kill switch policy, DNS leak protection, split tunneling, or VPN security controls on its own.

It should not need elevated privileges. VPN operations are delegated to Proton VPN CLI and the system services that CLI already uses.

## Project status

This project is early/alpha software. Expect rough edges, especially around desktop tray behavior, keyring integration, and Proton VPN CLI behavior changes.

Feedback from Fedora/KDE and Proton VPN Linux users is welcome.

## Development notes

See `CLAUDE.md` for architecture, the CLI abstraction, the detection strategy, and packaging notes.
