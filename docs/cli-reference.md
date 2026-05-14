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
WireGuard tunnel is up. Across the 12-case capture in
"Connect surface" below, wall time ranged from **2.5s to 5.7s**, with
Secure Core consistently slowest. (Earlier ~1.3-2.5s observations were
from country/city only; this is the full range.)

See "Connect surface" below for the empirical surface of every flag
and combination.

## Connect surface

Empirically captured on 2026-05-14 against `proton-vpn-cli 1.0.1`.
Each case below was preceded by `protonvpn disconnect` to reach a
known baseline. All twelve cases produced **exit 0 with empty
stderr**.

### Options surface

`protonvpn connect [OPTIONS] [SERVER_NAME]` — see the table near the
top of this doc for the option list. Combinations of `--country` with
exactly one of `{--p2p, --securecore, --tor, --random}` are valid
(cases #04-#07). Combinations between `--p2p`/`--securecore`/`--tor`
were **not** tested and may collide (each names a distinct server
pool); the preset UI should treat them as mutually exclusive. `--random`
appears orthogonal to the modifier flags and to `--country`/`--city`.

### Standard success output

For everything except Secure Core the CLI prints:

```
Connected to <SERVER_ID> in <city>, <country>.
Your new IP address is <ip>.
```

For Secure Core the second component of the location is the
**exit-country prefix** instead of the destination country:

```
Connected to SE-US#1 in New York, via Sweden.
```

`protonvpn status` mirrors this in the `Server:` line:

```
Server: SE-US#1 in New York, via Sweden
```

Parsers must accept both `, <country>.` and `, via <country>.` as
valid terminators of the location clause.

### Captures (all exit 0, empty stderr)

| # | Command                              | Selected server          | Wall  |
| - | ------------------------------------ | ------------------------ | ----- |
| 01| `connect --country US`               | US-WA#187 / Seattle      | 2.5s  |
| 02| `connect --city Seattle`             | US-WA#187 / Seattle      | 3.1s  |
| 03| `connect US-WA#187` (positional)     | US-WA#187 / Seattle      | 2.9s  |
| 04| `connect --country US --p2p`         | US-WA#298 / Seattle      | 3.2s  |
| 05| `connect --country US --tor`         | US-GA#29-TOR / Atlanta   | 3.4s  |
| 06| `connect --country US --securecore`  | SE-US#1 / NY via Sweden  | 4.8s  |
| 07| `connect --country US --random`      | US-IL#754 / Chicago      | 3.3s  |
| 08| `connect` (no args)                  | US-WA#187 / Seattle      | 3.4s  |
| 09| `connect --p2p`                      | US-WA#298 / Seattle      | 3.4s  |
| 10| `connect --securecore`               | IS-CA#1 / Montréal via IS| 5.7s  |
| 11| `connect --tor`                      | US-GA#29-TOR / Atlanta   | 3.0s  |
| 12| `connect --random`                   | US-CA#424 / San Jose     | 3.4s  |

Observations from the table:

- Bare `connect` and `connect --country US` both landed on
  `US-WA#187` here — "fastest globally" appears to be
  "fastest-near-client", not a worldwide selection.
- `--p2p` alone (case #09) gave the same server as
  `--country US --p2p` (case #04). When the fastest P2P server is
  already local, the `--country` constraint is a no-op.
- `--tor` and `--country US --tor` both landed in Atlanta — Tor is
  exposed on a small US-East subset (per `cities list US`, only
  Atlanta and Denver advertise the Tor feature).
- `--securecore` alone routed through Iceland; the `--country US`
  version forces the destination side to US (New York). Secure Core
  is the only flag observed to change the *visible* exit country.

### Server ID format variants

| Variant     | Example         | Pattern                              |
| ----------- | --------------- | ------------------------------------ |
| Standard    | `US-WA#187`     | `<country>-<state>#<n>`              |
| Tor         | `US-GA#29-TOR`  | `<country>-<state>#<n>-TOR`          |
| Secure Core | `SE-US#1`       | `<exit-country>-<dest-country>#<n>`  |

For Secure Core the two segments swap meaning: the first segment is
the *exit* (visible) country and the second is the destination, the
opposite of standard. A regex that only handles the standard pattern
will misread Secure Core IDs.

### Interface behavior

All 12 cases produced a `proton0` with
`<POINTOPOINT,NOARP,UP,LOWER_UP>` flags, exactly as documented in
"Network interface behavior". Interface index incremented
monotonically (9, 12, 15, 19, 22, 25, 28, 31, 35, 38, 41, 44) across
the captures — never reused, confirming the existing observation.

`ipv6leakintrf0` was observed in **10 of 12** captures. The two
exceptions (#03, #08) showed only `proton0` at the polling moment —
consistent with the existing "`ipv6leakintrf0` may appear slightly
after `proton0`" note. The interface predicate `^proton\d+$` UP is
unchanged.

### Implication for the preset model

The preset schema reduces to:

- **Target kind**: `none` | `country` | `city` | `server_id`
  (no `fastest` or `random` kind — "fastest" is the absence of a
  target, and `random` is a flag.)
- **Modifier flags**: at most one of `{p2p, secure_core, tor}` plus
  optional `random`.

Argv construction (matches the captured invocations exactly):

```
argv = ["protonvpn", "connect"]
if flags.p2p:         argv += ["--p2p"]
if flags.secure_core: argv += ["--securecore"]   # one word, no dash
if flags.tor:         argv += ["--tor"]
if flags.random:      argv += ["--random"]
if target.kind == "country":  argv += ["--country", target.value]
elif target.kind == "city":   argv += ["--city", target.value]
elif target.kind == "server_id": argv += [target.value]
# target.kind == "none" → no target argument
```

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

Single line. Exit code is **0** in both signed-in and signed-out
cases — see "Unauthenticated state" below. `info` is the canonical
auth-state probe.

## Unauthenticated state

Captured on 2026-05-13 after `protonvpn signout`, before signing back
in. Connection state was DISCONNECTED throughout. Auth state and
connection state are **orthogonal axes** — the CLI does not collapse
them into one signal.

### `info` (the canonical auth probe)

```
$ protonvpn signout
You have been successfully signed out.   # exit 0
$ protonvpn info
Account: 'None'                          # exit 0
```

Note: exit is **0** even when signed out. The discriminator is the
quoted account value: `'None'` ⇔ signed out, anything else ⇔ signed
in (and the value is the user's email). Regex:
`^Account:\s*'(?P<account>.+?)'\s*$` — then `account == "None"` means
signed-out.

### `status` (signed out)

```
$ protonvpn status
Status: Disconnected                     # exit 0
```

**Indistinguishable** from signed-in-but-disconnected. Do not use
`status` to detect auth.

### `connect` (signed out)

```
$ protonvpn connect --city Seattle
Server list is outdated, updating... This may take a moment.   # stdout
                                                               # exit 2, stderr:
Error: Authentication required.Please sign in with 'protonvpn signin' before connecting.

Try 'protonvpn connect --help' for more information.
```

Verbatim "Authentication required." (note: no space before "Please"
— that's the CLI's bug, not a typo here). Substring
`"Authentication required"` (case-insensitive) in stderr is the
reliable signal.

### `countries list` (signed out)

```
$ protonvpn countries list                                     # exit 2, stderr:
Error: Authentication required to view complete country list. Please sign in with 'protonvpn signin'
```

Same `"Authentication required"` substring.

### `config list` (signed out)

```
$ protonvpn config list                                        # exit 2, stderr:
Error: Authentication required to view feature status. Please sign in with 'protonvpn signin'
```

Same pattern.

### `disconnect` (signed out)

```
$ protonvpn disconnect
Disconnected.                            # exit 0
```

Always succeeds, even with no session.

### Implication for the detector

vpnpilot's detection layer treats **auth** and **connection** as
orthogonal axes:

| `info` says        | `proton\d+` interface | → state                          |
| ------------------ | --------------------- | -------------------------------- |
| `Account: 'None'`  | absent                | SIGNED_OUT + DISCONNECTED        |
| `Account: 'email'` | absent                | SIGNED_IN + DISCONNECTED         |
| `Account: 'email'` | present (UP)          | SIGNED_IN + CONNECTED            |
| `info` fails/timeout | (any)               | UNKNOWN (don't override prior)   |

(The fourth row — `'None'` while an interface is up — should be
impossible in practice; if observed, log a warning and treat as
SIGNED_OUT + CONNECTED, since the kernel doesn't lie.)

## servers / countries / cities

- `protonvpn servers`: prints a marketing URL only. **No useful output
  for the app.** Do not call.
- `protonvpn countries list`: table of countries with codes. Drive the
  country browser from this.
- `protonvpn cities list <COUNTRY>`: cities with features. Drive the
  city browser from this.

Both `countries list` and `cities list` are cheap and idempotent. The
session-cached strategy (cache in memory for the lifetime of the
process) is appropriate.

## Catalog surface

Captured on 2026-05-14 against `proton-vpn-cli 1.0.1` while signed in.

### `protonvpn countries list`

Exit 0. Stdout:

```
Country                           Code
--------------------------------  ------
Afghanistan                       AF
Albania                           AL
...
United States                     US
...
```

Format: two-column table. Line 1 is the header (`Country`, `Code`).
Line 2 is a separator of dashes (`-`). Lines 3+ are data rows, each
with the country name (may contain spaces) and the two-letter ISO code,
separated by two or more spaces. No server count column (unlike what
the top-level help implied). The name may contain multi-word names like
"Bosnia and Herzegovina" and "Democratic Republic of the Congo" — single
spaces within the name, always 2+ spaces before the code.

Timing: ~1.4s (wall). This is the round-trip cost for one subprocess
call. Countries list is fetched once and cached.

### `protonvpn cities list <COUNTRY>`

Exit 0 (for a valid, existing country code). Stdout:

```
Cities in United States:
City            Features
--------------  ----------
Ashburn         P2P
Atlanta         P2P, Tor
Boston          P2P
...
Salt Lake City
San Jose        P2P
Seattle         P2P
```

Format: line 1 is `Cities in <Country Name>:` (human-readable header,
not used for parsing). Line 2 is the column header (`City`, `Features`).
Line 3 is the separator. Lines 4+ are data rows.

The `Features` column is **optional** — some cities have no features
(e.g., `Salt Lake City`) and the trailing column is simply absent.
When present, features are comma-separated: `P2P`, `P2P, Tor`.

Observed feature values:
- `P2P` — P2P-optimised server
- `Tor` — Tor-over-VPN server (note capitalisation: initial cap, not all-caps)

City names may contain spaces (`New York`, `Los Angeles`, `Salt Lake City`).
City names may contain diacritics (`São Paulo`). The parser must handle
both. Country code normalised to uppercase by CLI (`us` → `US`).

Timing: ~1.4–1.5s per call (wall). Same subprocess cost as countries.
One call per country during prewarm; throttle to one at a time.

#### One-city country (Iceland)

```
Cities in Iceland:
City       Features
---------  ----------
Reykjavik  P2P
```

#### City with diacritics (Brazil)

```
Cities in Brazil:
City       Features
---------  ----------
São Paulo  P2P
```

#### Invalid country code (`ZZ`)

Exit 2. Stderr:

```
Error: Invalid country code 'ZZ'. Please use a valid country code.

Try 'protonvpn cities list --help' for more information.
```

Stdout is empty. The catalog service marks the entry as `FAILED` with
`last_error` set to this message.

#### Lowercase normalisation (`us` → `US`)

`protonvpn cities list us` succeeds identically to `cities list US`.
The CLI normalises the code internally. vpnpilot always passes uppercase
codes to avoid relying on this, but the normalisation is a safety net.

### Catalog when signed out

`countries list` exits 2 with the same "Authentication required" error
seen on `connect` and `config list` (see "Unauthenticated state" above):

```
Error: Authentication required to view complete country list. Please sign in with 'protonvpn signin'
```

The catalog service detects this via `is_auth_error` and exposes
`last_error`; the UI surfaces it rather than showing an empty list.

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
