# ProtonVPN CLI reference (as observed)

All findings below were captured by running the actual `protonvpn` CLI
on Fedora 44 in 2026-05. The CLI does **not** offer a `--json` flag or
a stable machine-readable output format, so all knowledge here is from
direct shell observation. The vpnpilot CLI abstraction layer should be
written against these observations, not against the spec, and re-tested
when the CLI version changes.

## Version

```
$ rpm -qf /usr/bin/protonvpn
proton-vpn-cli-1.0.1-1.fc44.noarch
```

The CLI prints its version inside the ASCII banner on
`protonvpn --help` ("1.0.1" at the end of the banner block). There is
no `--version` flag (`Error: No such option: --version`).

## Top-level commands

```
signin       Sign in (interactive: prompts password, may prompt TOTP)
signout      Sign out and clear local credentials
info         Show account email
connect      Connect to a server
disconnect   Disconnect
status       Show current connection status (text-only)
servers      Prints a URL to protonvpn.com (no machine-readable list!)
countries    Subcommand `list` — countries with code + server count
cities       Subcommand `list <COUNTRY>` — cities in a country
config       Subcommands `list` and `set <key> <value>`
```

Top-level options: `-v/--verbose`, `-h/--help`. Nothing else.

## connect

```
Usage: protonvpn connect [OPTIONS] [SERVER_NAME]
```

Flags:

| Flag                | Effect                                                 |
| ------------------- | ------------------------------------------------------ |
| `--country TEXT`    | Fastest in country (code `US` or name `"United States"`) |
| `--city TEXT`       | Fastest in city (quote multi-word: `"New York"`)         |
| `--p2p`             | Fastest P2P-optimized server                            |
| `-sc`, `--securecore` | Fastest Secure Core server                            |
| `--tor`             | Fastest Tor-over-VPN server                             |
| `--random`          | Random available server                                  |

`SERVER_NAME` positional accepts an ID like `IT#23`.

On success, the CLI prints **two** lines:

```
Connected to US-WA#347 in Seattle, United States. 
Your new IP address is 159.26.103.107.
```

The public IP line is parseable with a simple regex and means we do
**not** need an external IP lookup to surface the connected IP on a
fresh connect. (External lookup is still required if the user wants
to see the IP for a connection that was made outside the app — e.g.
when vpnpilot is launched into an already-connected state.)

`connect` is **blocking and synchronous** — it returns only after the
WireGuard tunnel is up. On WG it took ~1.3-2.5s in our tests.

## disconnect

```
Usage: protonvpn disconnect
```

Output on success: `Disconnected.` (single line).
Output when already disconnected: same line. Idempotent.

`disconnect` is also blocking; interface vanishes ~1.2s after the
command returns.

## status

When **connected**:

```
Status: Connected
Server: US-WA#232 in Seattle, United States
Load: 32%
Protocol: wireguard
```

When **disconnected**:

```
Status: Disconnected
```

(Single line. No `Server:`, `Load:`, `Protocol:` fields.)

First run after a state change can be preceded by:

```
Server list is outdated, updating... This may take a moment.
```

This is a one-shot CLI-internal cache refresh and may delay output by
1–2s. **Wrappers must use a generous timeout on `status` (≥10s)**.

The status format is **not** the primary source of truth for
detection — see "Detection strategy" below.

## config

`protonvpn config list` table:

```
Setting                  Value
-----------------------  -------
netshield                off
kill-switch              off
port-forwarding          off
custom-dns               off
vpn-accelerator          off
moderate-nat             off
ipv6                     off
anonymous-crash-reports  off
```

**There is no `protocol` setting.** WireGuard is the only protocol
this CLI version supports; the protocol shown in `status` cannot be
changed via `config set`. vpnpilot displays it read-only.

`protonvpn config set <setting> --help` reveals values:

- `kill-switch`: `off | standard`  — binary toggle
- `netshield`: `off | malware-only | malware-ads-trackers`
- (Other keys not yet enumerated — defer until we surface them in UI.)

After `config set ipv6 on/off`, the CLI reminds:

```
IPv6 has been set to enabled, please establish a new VPN connection
for changes to take effect.
```

Any setting change requiring a reconnect to take effect should be
exposed in the UI with that hint.

## info

```
$ protonvpn info
Account: 'tiffany.vonarnim@gmail.com'
```

Single line. If not signed in the CLI returns an error (exit code
non-zero) suggesting `protonvpn signin`. vpnpilot treats the latter
as the trigger for the "Not signed in" panel.

## servers / countries / cities

- `protonvpn servers`: prints a marketing URL only. **No useful output
  for the app.** Do not call.
- `protonvpn countries list`: table of countries with codes and server
  counts. Drive the country browser from this.
- `protonvpn cities list <COUNTRY>`: cities. Drive the city browser
  from this.

Both `countries list` and `cities list` are cheap and idempotent. The
session-cached strategy (cache in memory for the lifetime of the
process) is appropriate.

## Network interface behavior

Captured with `ip -o link show` polled at 5 Hz during cycles of
`disconnect` / `connect --city Seattle`. See
`docs/observations.md` for raw logs.

| Phase              | `proton0`             | `ipv6leakintrf0`      |
| ------------------ | --------------------- | --------------------- |
| Connected (steady) | UP, LOWER_UP, state UNKNOWN | UP, LOWER_UP, state UNKNOWN |
| Disconnect issued  | gone within ~1.2s     | gone within ~1.2s     |
| Disconnected       | absent                | absent                |
| Connect issued     | reappears UP within ~1.3-2.5s | reappears slightly after proton0 |
| Brief window       | already UP            | may appear with `state DOWN` for ~0.2s before going UP |

Key facts:

1. **Interface index increments per connection** (`11 → 15 → 18 → 21`
   across four reconnects). Never reused. Don't cache the index.
2. **`proton0` always comes up already UP.** There is no persistent
   "interface exists but DOWN" phase. The user-visible "transitioning"
   state in vpnpilot is therefore driven primarily by the in-flight
   command, not by an OS-level interface state.
3. **`ipv6leakintrf0` is unconditional.** It appears whenever
   `proton0` is up, regardless of whether `ipv6` is set to `on` or
   `off` in `protonvpn config`. The name is misleading; treat it as
   a co-interface, not as evidence about config state.
4. **Interface name pattern**: regex `^proton\d+$`. Multi-connection
   scenarios (not yet tested) may produce `proton1` etc.

## Detection strategy

vpnpilot's status detection layer is pluggable (see
`src/vpnpilot/detect.py`). The default composes two detectors:

1. **Primary — interface detector.** Reads `ip -o link show` (or
   `/sys/class/net/`). Connected ⇔ any interface matching
   `^proton\d+$` exists and has the `UP` flag. This is fast, cheap,
   and survives any future CLI output format change.
2. **Secondary — CLI status parser.** Calls `protonvpn status` and
   parses the first line. Used as confirmation, for surfacing the
   server name / load / protocol, and as a fallback if the interface
   check is somehow ambiguous.

State machine:

```
DISCONNECTED ──connect issued──> TRANSITIONING ──interface UP──> CONNECTED
CONNECTED    ──disconnect issued──> TRANSITIONING ──interface gone──> DISCONNECTED
```

The TRANSITIONING state is entered by vpnpilot itself when it issues a
command, **not** by observing an intermediate OS state. We exit it on
the next successful poll that confirms the post-state, or on command
error.

## Uptime

Read once on transition into CONNECTED:

```
stat -c %Y /sys/class/net/proton0   # link's "modify" time
```

Cache that timestamp as ground truth and compute uptime = `now - t0`.
Do **not** re-stat on every poll.

(`/sys/class/net/<iface>` reports its "Modify" time as the link-up
moment in our observations. Birth time is not exposed.)

## Public IP

Three sources, in preference order:

1. The `Your new IP address is X.X.X.X.` line printed by `connect` —
   captured immediately at the moment of connection. Free.
2. The configurable endpoint (`public_ip_endpoint` setting, default
   `"api.ipify.org"`, empty string disables) — used only when the app
   starts into an already-connected state, or on user-requested
   refresh.
3. Never poll on a timer.

## Privilege model

The `protonvpn` CLI handles its own polkit prompts. vpnpilot runs as
the unprivileged user and never invokes `sudo`, `pkexec`, or
NetworkManager directly.

## Sign-in

Sign-in is interactive (password + optional TOTP). vpnpilot does
**not** embed sign-in. When `protonvpn info` returns non-zero, the
app surfaces a panel showing the exact command
(`protonvpn signin <email>`) with a Copy button and a link to the
Proton documentation.

## Things to re-verify when the CLI is upgraded

- Existence of `protocol` as a `config set` key (if added, expose it).
- Presence of a `--json` flag on any command.
- Whether `servers` ever starts emitting machine-readable output.
- Whether `connect` gains a non-blocking mode.
- Whether the interface name pattern changes from `^proton\d+$`.
- Whether `status` adds public IP / uptime fields.
- Whether `info` reveals more than the email address.
