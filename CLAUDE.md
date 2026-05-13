# CLAUDE.md

Context for future Claude sessions working on this repo. Keep this
updated as decisions are made.

## What this is

vpnpilot is an **unofficial third-party** tray app that wraps the
official `proton-vpn-cli` on Linux. It is not affiliated with Proton
AG. The Proton branding/logos are not used. The app never runs with
elevated privileges; all privileged operations go through the CLI,
which handles polkit itself.

Current scope: **vertical slice** — tray icon (connected/disconnected/
transitioning), state detection, and Connect-to-Seattle / Disconnect
from the tray menu. No main window, settings UI, server browser, or
event log yet.

## Hard constraints

- **No root.** Never invoke `sudo`/`pkexec`/NetworkManager directly.
  The CLI's polkit dialog is the only privilege boundary.
- **No Proton branding.** Icons and copy are generic.
- **Graceful degradation.** Missing CLI = clear error dialog and quit
  cleanly. Missing tray = clear instruction (link to AppIndicator
  extension on GNOME).
- **No secret logging.** Never read `~/.config/Proton/`. Logs contain
  the user's own actions and state transitions only.
- **Subprocess timeouts everywhere.** No unbounded `wait()`.

## Architecture

```
                 ┌───────────────────────────────────────────────┐
                 │                 vpnpilot.app                   │
                 │  (Qt + qasync bootstrap, signal wiring)        │
                 └────┬───────────────────────────┬───────────────┘
                      │                           │
                      ▼                           ▼
               vpnpilot.tray             vpnpilot.controller
        ┌──────────────────────┐      ┌────────────────────────┐
        │ QSystemTrayIcon UI   │◀─────│ owns canonical state,  │
        │ menu, icon swap,     │ Qt   │ runs poll loop, emits  │
        │ tooltip, balloons    │signals state_changed/error    │
        └──────────────────────┘      └──┬──────────────────┬──┘
                                         │                  │
                                         ▼                  ▼
                              vpnpilot.detect      vpnpilot.cli
                          ┌───────────────────┐  ┌────────────────┐
                          │ Detector ABC      │  │ ProtonCLI      │
                          │  InterfaceDetector│  │  async wrapper │
                          │  CLIStatusDetector│  │  parsers       │
                          │  CompositeDetector│  └────────────────┘
                          └───────────────────┘
```

Module roles:

- `state.py` — `ConnState` enum and the `ConnectionInfo` dataclass that
  flows through every layer.
- `cli.py` — async wrapper around the `protonvpn` binary, with timeouts
  and structured `CLIResult`. Parsers for `status` and `connect` output
  live next to the regex they target so a CLI upgrade only touches the
  parser, not the wrapper.
- `detect.py` — pluggable detection (see "Detection strategy" below).
- `controller.py` — single source of truth for app state. Runs the
  polling loop, owns the "in-flight command" guard, emits Qt signals.
- `tray.py` — only file that knows about menus, icons, and KDE/GNOME
  tray quirks. Reacts to controller signals.
- `app.py` — bootstrap: QApplication + qasync event loop + DI.
- `_qasync_shim.py` — prefers system-installed `qasync`, falls back to
  the vendored copy under `_vendor/qasync`.
- `_vendor/qasync/` — bundled BSD-2-Clause qasync (not in Fedora repos).
  **Do not edit vendored code**; lint excludes this path.

## The CLI abstraction

`ProtonCLI` is async; every call goes through `_run` which:

1. Spawns the subprocess with stdout/stderr pipes.
2. Awaits `proc.communicate()` with a per-call timeout (default 30 s,
   but `status` and `connect` extend it because the CLI's first call
   after a state change does an internal server-list refresh — see
   `docs/cli-reference.md`).
3. On timeout, kills the process and returns a `CLIResult(timed_out=True)`.
4. On `FileNotFoundError`, returns `returncode=127` so the UI layer
   can handle "CLI not installed" uniformly.

Output parsing is regex-based against the *exact* strings captured in
`docs/cli-reference.md`. The CLI offers no `--json`. Tests assert
against those captured strings, so a CLI format change is caught by
the test suite.

**Never invoke the real CLI in tests.** `tests/test_cli_subprocess.py`
patches `asyncio.create_subprocess_exec`. Live verification is a
manual `make run`, not a test.

## Detection strategy

Detection is split into composable `Detector` implementations:

1. **`InterfaceDetector` (primary)** — reads `/sys/class/net/`; falls
   back to parsing `ip -o link show` if sysfs is unavailable. The
   "connected" predicate is `any interface matching ^proton\d+$ AND
   has IFF_UP (0x1) set`. This is what survives CLI format changes.
2. **`CLIStatusDetector` (secondary)** — invokes `protonvpn status`
   and parses the text output for enrichment (server name, city,
   country, load, protocol).
3. **`CompositeDetector`** — uses the interface signal as truth, then
   runs the CLI detector for enrichment when connected. A stale CLI
   status saying "connected" while the interface is gone is **not**
   treated as connected — the kernel is the source of truth.

`default_detector(cli)` returns the composite.

### Transitioning state

The kernel never observably holds `proton0` in a DOWN state — it goes
straight from absent to UP. The user-visible **TRANSITIONING** state
is therefore set by the *controller* when it issues a `connect` or
`disconnect`, not by reading kernel state. Controller flow:

```
issue command  →  set TRANSITIONING  →  await CLI return
              →  refresh_state via detector  →  set new state
```

The poll loop won't trample the TRANSITIONING indicator because of the
`_in_flight` guard; the post-command `_refresh_state` bypasses it.

### Uptime (not yet wired in slice)

When a transition into CONNECTED happens, capture
`stat -c %Y /sys/class/net/<iface>` once and cache. Compute uptime as
`now - t0`. Do not stat per poll.

### Public IP

Three sources, in preference order:

1. The `Your new IP address is X.` line from `connect`. Free, no
   network call. Captured at the moment of connection.
2. The `public_ip_endpoint` setting (default `"api.ipify.org"`, empty
   string disables). Used only when vpnpilot is launched into an
   already-connected state, or on user-requested refresh.
3. Never poll.

## qasync

Not in Fedora repos. Vendored under `src/vpnpilot/_vendor/qasync` (BSD-2,
unchanged from upstream 0.28.0). The shim
`vpnpilot._qasync_shim` prefers `import qasync` (e.g. dev venv) and
falls back to the vendored copy at runtime. The RPM has no out-of-repo
Requires.

If qasync ever lands in Fedora repos, add it to `Requires:` in the spec
and the vendored copy can stay as fallback or be removed.

## Settings & persistence (not yet implemented in slice)

When added:

- Schema: pydantic v2 model.
- File: `~/.config/vpnpilot/settings.json` (XDG config).
- Event log: `~/.local/state/vpnpilot/events.log` (XDG state), rolling
  ~500 entries. Never logs CLI output verbatim — only events:
  `2026-05-13T22:00:01Z user_action connect city=Seattle ok=true`.

## Run / test / package

```sh
make install-dev   # python3 -m venv .venv; pip install -e .[dev]
make run           # python -m vpnpilot from the venv
make test          # pytest (29 tests; mocks subprocess, fakes sysfs)
make lint          # ruff (excludes src/vpnpilot/_vendor)
make rpm           # builds ./dist/vpnpilot-<v>-<n>.fc44.noarch.rpm
make clean         # removes dist/, .rpmbuild/, __pycache__/
```

### Building the RPM from scratch on a fresh Fedora 44

```sh
sudo dnf install -y rpm-build python3-devel pyproject-rpm-macros desktop-file-utils
make sdist
# The first `make rpm` will fail with a list of needed BuildRequires:
make rpm  # writes .rpmbuild/SRPMS/...buildreqs.nosrc.rpm
sudo dnf builddep -y .rpmbuild/SRPMS/vpnpilot-*-buildreqs.nosrc.rpm
make rpm  # now succeeds
sudo dnf install -y ./dist/vpnpilot-*.noarch.rpm
```

After install, `/usr/bin/vpnpilot` is the entry point. The `.desktop`
file is at `/usr/share/applications/vpnpilot.desktop`; icon at
`/usr/share/icons/hicolor/scalable/apps/vpnpilot.svg`.

### Runtime requirements (host)

- `proton-vpn-cli` (the RPM has this as a hard `Requires:`).
- `python3-pyqt6` (hard `Requires:`).
- Working system tray:
  - **KDE Plasma 6**: built-in, no extension needed.
  - **GNOME**: AppIndicator extension required. RPM has it as
    `Recommends:` but cannot install it from RPM (Fedora doesn't ship
    that extension). vpnpilot detects its absence at startup and
    shows the install URL.

## Things not yet built (deliberate scope cut)

- Main window with status/server/IP/uptime/quick-connect button.
- Quick-connect preset library (only "Seattle" is hardcoded today).
- Country/city server browser.
- Settings UI (kill-switch toggle, autoconnect, start-minimized,
  preferred preset, public-IP endpoint).
- Auto-connect on login (XDG autostart with `--autoconnect`).
- Connection event log with ~500-entry rolling persistence.
- Sign-in detection panel (today the app assumes signed-in; CLI
  errors are surfaced as toast notifications, which is enough for the
  slice).

These should be added one at a time, each a separate logical commit,
each with tests. Keep `docs/cli-reference.md` updated whenever a new
CLI command/format is observed.

## Things to verify on every CLI upgrade

Run the experiment in `docs/observations.md` and update
`docs/cli-reference.md` if any of these change:

- Output format of `status` (the four-line connected format, the
  one-line disconnected format).
- Output format of `connect` (the "Connected to ..." and "Your new IP
  ..." lines).
- Whether `--json` exists on any subcommand.
- Whether `servers` returns machine-readable output (today it just
  prints a URL).
- Whether `config set protocol` becomes available.
- Whether the interface name pattern changes from `^proton\d+$`.
- Whether `ipv6leakintrf0` ever becomes conditional on the ipv6
  setting (today it's unconditional).
