# Raw observation logs

The transition timing notes in `cli-reference.md` were derived from a
single observation run (5 Hz polling of `ip -o link show`, interleaved
with checkpointed `protonvpn` commands). Raw logs are below for reference.

## Method

```sh
# Poller (every 200 ms):
while true; do
  printf "TS=%s " "$(date +%s.%3N)"
  ip -o link show | grep -Eo '[0-9]+: (proton[0-9]+|[a-z0-9]*leak[a-z0-9]*): <[^>]+> [^ ]+ [^ ]+ [^ ]+ [^ ]+ state [A-Z]+' | tr '\n' '|'
  echo
  sleep 0.2
done
```

Interleaved foreground commands (with checkpoints written to a
separate log):

1. baseline (was connected to US-WA#232 Seattle, wireguard)
2. `protonvpn disconnect`
3. `protonvpn connect --city Seattle` (ipv6=off)
4. `protonvpn config set ipv6 on`
5. `protonvpn disconnect`
6. `protonvpn connect --city Seattle` (ipv6=on)
7. `protonvpn config set ipv6 off`  (restore)
8. `protonvpn disconnect`
9. `protonvpn connect --city Seattle`  (restore)

## Notable transitions from the poller

Cycle 1 — disconnect then connect, ipv6=off:

```
TS=1778715030.766  proton0 UP|ipv6leakintrf0 UP|     <-- last "connected" tick
TS=1778715030.971  (empty)                            <-- disconnect took effect
TS=1778715035.457  proton0(idx 15) UP|                <-- connect: proton0 first
TS=1778715035.661  proton0 UP|                        <-- still alone (0.2s)
TS=1778715035.865  proton0 UP|ipv6leakintrf0 UP|      <-- both present
```

Cycle 2 — disconnect then connect, ipv6=on (the only run where the
brief "leak iface DOWN" window was visible):

```
TS=1778715045.696  proton0(idx 18) UP|
TS=1778715045.900  proton0 UP|
TS=1778715046.105  proton0 UP|ipv6leakintrf0 DOWN|    <-- 200ms window
TS=1778715046.311  proton0 UP|ipv6leakintrf0 UP|
```

## CLI text output samples

`disconnect` (always):

```
Disconnected.
```

`connect --city Seattle` (success):

```
Connected to US-WA#347 in Seattle, United States. 
Your new IP address is 159.26.103.107.
```

`status` (connected):

```
Status: Connected
Server: US-WA#232 in Seattle, United States
Load: 32%
Protocol: wireguard
```

`status` (disconnected):

```
Status: Disconnected
```

`info`:

```
Account: 'tiffany.vonarnim@gmail.com'
```

`config set ipv6 on`:

```
IPv6 has been set to enabled, please establish a new VPN connection for changes to take effect.
```

Note also the one-shot first-run preamble that may precede `status`
after CLI restart or state change:

```
Server list is outdated, updating... This may take a moment.
```

## Unauthenticated capture run

Run on 2026-05-13 after `protonvpn signout`.

```
$ protonvpn signout
You have been successfully signed out.   # exit 0

$ protonvpn status
Status: Disconnected                     # exit 0

$ protonvpn info
Account: 'None'                          # exit 0

$ protonvpn connect --city Seattle       # exit 2
[stdout] Server list is outdated, updating... This may take a moment.
[stderr] Error: Authentication required.Please sign in with 'protonvpn signin' before connecting.
[stderr]
[stderr] Try 'protonvpn connect --help' for more information.

$ protonvpn countries list               # exit 2
[stdout] Server list is outdated, updating... This may take a moment.
[stderr] Error: Authentication required to view complete country list. Please sign in with 'protonvpn signin'
[stderr]
[stderr] Try 'protonvpn countries list --help' for more information.

$ protonvpn disconnect                   # exit 0
Disconnected.

$ protonvpn config list                  # exit 2
[stderr] Error: Authentication required to view feature status. Please sign in with 'protonvpn signin'
[stderr]
[stderr] Try 'protonvpn config list --help' for more information.
```

Key observations:

1. `protonvpn info` returns **exit 0** even when signed out, with
   stdout `Account: 'None'`. The literal string `'None'` is the
   signed-out marker.
2. `protonvpn status` is identical to signed-in-disconnected; cannot
   be used to detect auth.
3. Operational commands (`connect`, `countries list`, `config list`)
   exit 2 with stderr containing `Authentication required` (the exact
   wording varies per command — sometimes "Authentication required.",
   sometimes "Authentication required to view ...").
4. `disconnect` is idempotent and succeeds even with no session.
