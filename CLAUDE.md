# CLAUDE.md

Context for future Claude sessions working on this repo. Keep this
updated as decisions are made.

## What this is

vpnpilot is an **unofficial third-party** tray app that wraps the
official `proton-vpn-cli` on Linux. It is not affiliated with Proton
AG. The Proton branding/logos are not used. The app never runs with
elevated privileges; all privileged operations go through the CLI,
which handles polkit itself.

Current scope: tray icon with four UI states (connected, disconnected,
transitioning, signed-out), state detection on two orthogonal axes
(connection + auth), modeless Sign-in panel, **modeless main window**
with status panel + tabbed panel (Presets | Browse) + disconnect footer,
a **user-editable preset library** seeded with a Seattle entry on first
run, an in-memory **server catalog** (country + city list from the CLI,
lazy-fetch + background prewarm, `vpnpilot catalog dump` diagnostic
command), and a **server browser tab** (two-pane country/city view with
filters, feature badges, server-ID escape hatch). Preset editor Country
and City fields are now catalog-backed editable comboboxes. The tray's
connect section is dynamic — top entry is the default preset, a
"Connect to…" submenu lists the rest. No settings UI or event log yet.

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

## Design principles

- **Parse semantically, not by exit code.** The CLI exits 0 for cases
  we care about distinguishing (signed-in vs. signed-out
  `info`; idempotent `disconnect`), and exits non-zero with
  `Authentication required` in stderr for cases that look like
  "failures" but mean "you're signed out." Decisions about auth and
  connection state must read the printed text, not the return code.
  See `parse_info`, `is_auth_error`, and `parse_status` in `cli.py`.
- **Disconnect is never gated.** Connect actions are blocked when
  `auth == SIGNED_OUT` (we know it would fail), but disconnect is
  always callable — at the CLI it idempotently no-ops, and we want it
  available as a recovery path if detection is wrong (e.g. tunnel up
  but auth probe says signed out, which the kernel-as-truth detector
  could still report as connected). This asymmetry is deliberate and
  enforced both in `Controller.disconnect` and in every UI surface
  (tray, main window footer).
- **Kernel is truth for connection state.** `^proton\d+$` UP wins over
  any `protonvpn status` claim — see "Detection strategy".
- **Preset = target × flags.** Where (country / city / server ID / nothing)
  is orthogonal to how (P2P / Secure Core / Tor / random). Validate this
  shape at construction; never store an incoherent combination on disk.

## Architecture

```
                 ┌───────────────────────────────────────────────┐
                 │                 vpnpilot.app                   │
                 │  (Qt + qasync bootstrap, DI, signal wiring)    │
                 └──┬──────────────┬─────────────┬───────────────┘
                    │              │             │
                    ▼              ▼             ▼
            vpnpilot.tray  vpnpilot.controller  vpnpilot.preset
        ┌────────────────┐ ┌───────────────────┐ ┌─────────────────┐
        │ QSystemTrayIcon│◀┤ canonical state,  ├─│ Preset model    │
        │ dynamic menu   │Qt│ poll loop, in-   │ │ PresetStore     │
        │ rebuilt from   │  │ flight guard,    │ │ (JSON, 0600)    │
        │ PresetStore on │  │ connect_preset() │ └─────────────────┘
        │ aboutToShow    │  │ via store        │         ▲
        └───────┬────────┘  └──┬────────────┬──┘         │
                │              │            │             │
                │              ▼            ▼             │
                │      vpnpilot.detect  vpnpilot.cli      │
                │   ┌──────────────────┐ ┌─────────────┐  │
                │   │ Detector ABC     │ │ ProtonCLI   │──┼───────────┐
                │   │  Interface       │ │ async +     │  │           │
                │   │  CLIStatus       │ │ parsers,    │  │           ▼
                │   │  Auth            │ │ run_command │  │  vpnpilot.catalog
                │   │  Composite       │ └─────────────┘  │  ┌────────────────┐
                │   └──────────────────┘                  │  │ ServerCatalog  │
                ▼                                         │  │ (QObject,      │
        vpnpilot.main_window ─────────────────────────────┤  │  lazy-fetch +  │
        ┌──────────────────────────────────────────┐      │  │  prewarm,      │
        │ StatusPanel (state, server, protocol,    │      │  │  catalog_changed│
        │   auth indicator)                        │      │  │  signal,       │
        │ QTabWidget:                              │      │  │  non-triggering│
        │   "Presets" tab:                         │      │  │  accessors)    │
        │     PresetListPanel (QListView +         │      │  └────────────────┘
        │       Connect/Edit/Delete/New/Set-Default│      │         ▲
        │       + uses PresetEditorDialog)         │──PresetStore   │
        │   "Browse" tab:                          │      │         │
        │     BrowseTab (vpnpilot.browser) ────────┼──────┘  (catalog)
        │       countries pane + cities pane +     │
        │       server-ID field + Refresh btn      │
        │ Disconnect footer                        │
        └──────────────────────────────────────────┘
                ▲
                │ instantiated by tray on left-click
                │ or "Open VPNPilot…" menu item
                │
        vpnpilot.signin_panel (modeless dialog from tray)
```

Module roles:

- `state.py` — `ConnState` enum, `AuthState` enum, and the
  `ConnectionInfo` dataclass that flows through every layer. Connection
  and auth are **orthogonal axes**: every snapshot carries both.
- `cli.py` — async wrapper around the `protonvpn` binary, with timeouts
  and structured `CLIResult`. Parsers for `status` and `connect` output
  live next to the regex they target so a CLI upgrade only touches the
  parser, not the wrapper.
- `detect.py` — pluggable detection (see "Detection strategy" below).
- `controller.py` — single source of truth for app state. Runs the
  polling loop, owns the "in-flight command" guard, emits Qt signals.
  `connect_preset(preset_id)` looks up via injected `PresetStore`,
  translates target/flags into CLI kwargs (see `preset_to_connect_kwargs`).
  `connect_to_location(country_code, city=None)` and
  `connect_to_server_id(server_id)` are the browser's connect paths;
  they route through the same `_do_connect(**kwargs)` path. Server IDs
  are validated against `^[A-Z]{2}(-[A-Z]{2,3})?#\d+(-TOR)?$` before
  shelling out (`connect_to_server_id` raises `ValueError` on mismatch).
- `preset.py` — `Preset` dataclass, `PresetStore` (JSON-backed list with
  the invariants `len >= 1`, exactly one `is_default`, unique names).
  Pure Python, no Qt. Translates to CLI kwargs at the boundary.
- `tray.py` — only file that knows about menus, icons, and KDE/GNOME
  tray quirks. Reacts to controller signals. **Connect section is
  dynamic** — rebuilt from `PresetStore` on `QMenu.aboutToShow`. Top
  entry is "Connect to <default>"; non-default presets live in a
  "Connect to…" submenu. Left-click on the tray icon opens
  `MainWindow`. Auth-state takes priority over connection-state in
  the render switch — see "Auth axis" below.
- `main_window.py` — modeless `MainWindow` (status panel + `QTabWidget`
  ["Presets" | "Browse"] + disconnect footer). `PresetListModel` is a
  `QAbstractListModel` over the store. `PresetListPanel` owns the list
  view and action buttons; takes action callbacks at construction so it
  doesn't depend on `Controller` or `PresetEditorDialog` directly.
  The Browse tab is `BrowseTab` from `browser.py` — added only when
  catalog is provided (always in the full app, optional in tests).
  Hide-on-close so reopen is cheap.
- `browser.py` — `BrowseTab(QWidget)`: catalog-backed two-pane country/
  city view. **Secondary discovery path — presets are still the primary
  daily-use connect surface.** `CountryListModel` / `CityListModel` are
  `QAbstractListModel` implementations; `QSortFilterProxyModel` handles
  both pane filters. Widget never awaits catalog coroutines — schedules
  tasks via `asyncio.get_running_loop().create_task()` and reacts to
  `catalog_changed` signals for incremental updates. Signed-out state
  shown via `QStackedWidget` (auth page / normal page).
- `preset_editor.py` — modal `PresetEditorDialog` for both New… and
  Edit…. Storage-agnostic: takes the existing `Preset | None`, a set of
  taken names, and an optional `catalog`. Without catalog, behavior is
  identical to previous slices. With catalog, Country and City fields are
  editable `QComboBox` backed by `catalog.countries_if_ready()` and
  `catalog.cities_if_loaded()`; free-text always accepted as fallback.
  Caller persists via `PresetStore`.
- `signin_panel.py` — modeless `SignInPanel(QDialog)` shown when the
  user clicks the tray's "Sign in…" item. Knows nothing about the CLI
  or asyncio — takes `on_recheck` callable and a `state_signal`,
  closes itself when it observes `AuthState.SIGNED_IN`.
- `user_state.py` — `JsonStateStore` persists *observed* state (last
  signed-in email) to `~/.config/vpnpilot/state.json` (0600). This is
  distinct from `presets.json` (user-curated presets) and from
  `settings.json` (user preferences, not yet implemented).
- `catalog/` — `ServerCatalog(QObject)`: in-memory, session-scoped country
  and city catalog. `async countries()` caches on first call; `cities(code)`
  returns a `CatalogEntry` immediately and kicks off a background fetch if the
  entry is `NOT_FETCHED`; `cities_async(code)` awaits the in-flight task;
  `cities_sync(code)` is a blocking wrapper for non-Qt callers (dump command,
  sync tests). `prewarm()` walks all countries sequentially (one at a time —
  see "Prewarm timing" below). `refresh()` drops all cached state.
  `catalog_changed(country_code)` signal notifies listeners of state transitions.
  Non-triggering read-only accessors: `countries_if_ready()`, `cities_if_loaded(code)`,
  `entry_state(code)`, `entry_error(code)` — used by the browser and preset editor
  to read cached data without side effects. `catalog/_cli.py` implements
  `vpnpilot catalog dump` (JSON to stdout, requires a QCoreApplication). Parsers
  live in `catalog/parser.py`; models in `catalog/models.py` — no Qt, no subprocess.
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

## Catalog lazy-fetch and prewarm strategy

`ServerCatalog` uses two-phase loading:

1. **Lazy per-country fetch** — `cities(code)` returns a `CatalogEntry`
   immediately with `state = NOT_FETCHED` (or whatever the current state is)
   and kicks off a background `asyncio.Task` if the entry hasn't been fetched.
   Concurrent calls for the same code share the same task (dedup).

2. **Background prewarm** — `prewarm()` walks all countries sequentially,
   calling `cities_async(code)` for each and awaiting completion before moving
   on. One country at a time, no parallelism, so we don't hammer the Proton API.
   Each country's cities take ~1.4–1.5s to fetch (one subprocess call); with
   ~130 countries the full prewarm takes several minutes, but the data is
   available lazily before the prewarm finishes.

**Prewarm timing:** prewarm starts on the first `controller.state_changed`
signal (i.e., after the first successful connection-state poll). This avoids
contending with the autoconnect CLI call at app startup: both paths shell out
to the protonvpn CLI, and concurrent subprocess calls slow everything down.
The one-shot flag in `app.py` ensures prewarm fires exactly once per process.

The 1.4–1.5s per-country timing was measured for `countries list` and
`cities list`; the cost is the subprocess round-trip to the CLI (which calls
the Proton API internally). This is not a vpnpilot design choice — it's the
CLI's cost. If the CLI adds a `--json` or a cached local server list in a
future version, revisit this.

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

### Auth axis

Auth is detected via `protonvpn info`, which prints `Account: 'None'`
(literal string) when signed out and `Account: '<email>'` when signed
in — exit code is **0 in both cases** (see `docs/cli-reference.md`).
`AuthDetector.probe()` returns `(AuthState, email | None)`. The
`CompositeDetector` runs the auth probe concurrently with the
interface check; both feed into a single `ConnectionInfo`.

Controller gating:

- `connect_preset(preset_id)` is a **no-op + error signal** when
  `auth == SIGNED_OUT`. Saves the round-trip and surfaces a clean
  message instead of relying on the CLI's "Authentication required."
  stderr.
- `disconnect` is intentionally **not** gated — see "Disconnect is
  never gated" in the Design principles section.

Tray rendering: `auth == SIGNED_OUT` takes priority over the
connection axis. The tray switches to the signed-out icon, replaces
the disabled connect entries with a visible "Sign in…" item, and
offers the panel as the only useful action. The main window's status
panel shows the same auth indicator (small), but keeps the rest of
the UI visible (with Connect disabled) so the user can still curate
the preset list while signed out.

### Sign-in flow

The CLI's `signin` is interactive (password, TOTP, captcha). We do
not embed it. The panel:

1. Pre-fills the suggested command using the last-known email from
   `JsonStateStore` (`<email>` placeholder if first launch).
2. Provides a Copy button that puts `protonvpn signin <email>` on
   the clipboard.
3. Auto-rechecks every 5 seconds via `Controller.force_refresh()`.
4. Closes itself the moment a state-change with `AuthState.SIGNED_IN`
   arrives.

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

## Settings & persistence

Three files in `~/.config/vpnpilot/`, separated by intent. All are
0600, all use atomic tmp+rename writes, all tolerate missing / empty
files. Each file is a JSON object with a `"version"` integer when
forward-compatibility matters.

- **Observed state**: `state.json` — what the app last saw the CLI
  report, currently just `{"last_email": "..."}`. Managed by
  `JsonStateStore` (`user_state.py`).
- **User-curated presets**: `presets.json` — the preset library.
  Managed by `PresetStore` (`preset.py`). On a corrupt file the store
  renames to `presets.json.corrupt` and reseeds with the Seattle
  default. Invariants: `len(presets) >= 1`, exactly one
  `is_default == true`, names unique. Default preset is always at
  index 0 of `list_all()`.
- **User settings (not yet implemented)**: `settings.json`. Will hold
  toggles like kill-switch, autoconnect, public-IP endpoint. Will be a
  pydantic v2 model.

Event log (not yet implemented): `~/.local/state/vpnpilot/events.log`
(XDG state), rolling ~500 entries. Never logs CLI output verbatim —
only events:
`2026-05-13T22:00:01Z user_action connect preset=Seattle ok=true`.

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

- Uptime in the status panel (capture `stat -c %Y /sys/class/net/proton0`
  on transition into CONNECTED, compute `now - t0`).
- Public-IP display in the status panel (the `connect` line is already
  parsed; the wire-up to the status panel hasn't landed).
- Feature filter chips in the browser cities pane (badges are read-only;
  filtering by P2P/Tor not yet wired).
- Right-click "Save as Preset" from the browser (drag-from-browser-to-presets
  is also not built).
- Favorites / recently-used in the browser.
- City filter in preset editor's city combobox (the combobox is editable but
  has no filter proxy; typing filters are handled by QComboBox's built-in
  completion rather than a separate QSortFilterProxyModel).
- Settings UI (kill-switch toggle, autoconnect, start-minimized,
  preferred preset, public-IP endpoint).
- Auto-connect on login (XDG autostart with `--autoconnect`).
- Connection event log with ~500-entry rolling persistence.
- Drag-to-reorder presets in the main window list (current display
  order is creation order with the default pinned to index 0).
- Status/connect parsers handle the Secure Core `, via <country>` form
  (see `docs/cli-reference.md` "Server ID format variants" — the
  existing regex tolerates it but stores "via X" in the country field,
  which is wrong cosmetically; functionally still works).

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
