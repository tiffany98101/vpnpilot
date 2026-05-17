Context: This is for my VPN Pilot project at:

  /home/administrator/organized_projects/vpnpilot

GitHub repo:

  git@github.com:tiffany98101/vpnpilot.git

Yesterday and today we investigated and fixed a serious graphical-login stability problem on Fedora KDE/GNOME that appeared related to VPN Pilot.

Summary of what happened:

Yesterday / initial state:
- VPN Pilot was installed and configured as a user systemd service:
    ~/.config/systemd/user/vpnpilot.service
- It was enabled for:
    graphical-session.target
- The unit originally used:
    ExecStart=/usr/bin/vpnpilot
    Restart=on-failure
    RestartSec=3
- That meant VPN Pilot started automatically during desktop login.
- The installed service was using the old system copy at /usr/bin/vpnpilot, while my patched/user install lives at:
    /home/administrator/.local/bin/vpnpilot

Today’s investigation:
- GNOME and KDE both crashed or returned to the login greeter during login attempts.
- Journal logs showed VPN Pilot starting during the fragile desktop-session startup window.
- VPN Pilot repeatedly exited with status=1 and systemd restarted it several times.
- Relevant pattern:
    vpnpilot.service started
    app exited status=1/FAILURE
    systemd restarted it
    this repeated during login
- GNOME coredumps were present, but VPN Pilot itself did not coredump.
- The likely mechanism was that VPN Pilot was contributing to instability by doing Qt/DBus/portal/tray work and ProtonVPN CLI work during graphical login, then being restarted by systemd.

Root causes found:
1. Tray-unavailable startup returned exit code 1.
   - In src/vpnpilot/app.py, if tray support was not available, VPN Pilot exited with failure.
   - Because the systemd service had Restart=on-failure, that created a restart loop.

2. Startup catalog prewarm was too expensive.
   - app.py connected controller.state_changed to catalog.prewarm().
   - The first status refresh emitted state_changed.
   - catalog.prewarm() loaded countries and then called cities_async() for every country.
   - cities_async() ran commands like:
       protonvpn cities list BY
   - That produced heavy CPU/memory use and child ProtonVPN processes during desktop login.

3. ProtonVPN status/info calls were too aggressive.
   - protonvpn status originally had an effectively long timeout because the CLI default timeout was 30s.
   - The controller poll interval was only 3 seconds.
   - Even after adding timeouts, the app still launched repeated fresh protonvpn info/status probes during startup.

4. Background polling was too frequent for this CLI.
   - ProtonVPN CLI calls are expensive on this system.
   - Even when stable, polling every 120 seconds added noticeable CPU use.
   - We made the interval user-configurable.

Fixes implemented:
- Changed tray-unavailable startup so it exits cleanly instead of returning failure.
- Removed automatic catalog prewarm at startup.
- Kept country/city catalog loading lazy, triggered only by user interaction.
- Added explicit 10-second timeouts for:
    protonvpn info
    protonvpn status
    protonvpn countries list
    protonvpn cities list <country>
- Improved timeout cleanup:
    kill subprocess
    wait briefly
    return safe failed result
- Added refresh coalescing so overlapping refreshes share one in-flight detector run.
- Added startup delay:
    STARTUP_POLL_DELAY_SECONDS = 30.0
- Increased default background poll interval.
- Added refresh cooldown:
    MIN_REFRESH_INTERVAL_SECONDS = 30.0
- Added user-configurable refresh interval choices:
    Manual only
    Every 2 minutes
    Every 5 minutes
    Every 10 minutes
    Every 30 minutes
- Default background refresh is now:
    10 minutes / 600 seconds
- Preference is persisted through JsonStateStore as:
    poll_interval
- Invalid persisted values fall back to 10 minutes.
- Runtime interval changes reschedule polling without launching an immediate ProtonVPN CLI probe.
- Manual “Refresh now” bypasses cooldown.
- Connect/disconnect post-action refreshes still force immediate refresh.

Systemd/user install fixes:
- Added a user-service override so systemd runs the patched user copy:
    ExecStart=/home/administrator/.local/bin/vpnpilot
- Added safer restart behavior:
    Restart=on-failure
    RestartSec=10
    StartLimitIntervalSec=120
    StartLimitBurst=2
- Reinstalled VPN Pilot with:
    python3 -m pip install --user --upgrade .
- Confirmed systemd uses:
    /home/administrator/.local/bin/vpnpilot

Validation performed:
- Full tests passed multiple times.
- Final test result:
    265 passed in ~1.45s
- Manual systemd startup tests showed:
    no ProtonVPN child processes during first 30 seconds
    no cities list process
    no repeated info/status storm
    no restart loop
    service stayed active
    memory stabilized
    CPU stayed reasonable
- Final long sample showed RSS essentially flat:
    about 185 MB RSS across repeated samples
- No child ProtonVPN processes were left running after polls.
- No new journal spam except a non-fatal Qt/portal warning:
    Failed to register with host portal QDBusError(... Connection already associated with an application ID)

Commits pushed to GitHub:
- 3845153 Make VPN Pilot startup safe for graphical login
- 8fc39ed Make VPN Pilot startup safe for graphical login
- 343df18 Make VPN Pilot safe for graphical login startup
- f1b5897 Add configurable refresh interval

Final GitHub state:
- Local main and origin/main are in sync.
- Latest commit:
    f1b5897 Add configurable refresh interval
- git status was clean.
- Push succeeded:
    cce5dd5..f1b5897 main -> main

Current conclusion:
- The original login-crash risk appears resolved.
- VPN Pilot is enabled again as a user service.
- It now starts safely during graphical login.
- It waits before first ProtonVPN CLI probe.
- It no longer prewarms city/country catalogs at startup.
- It no longer polls every 3 seconds.
- Background refresh is user-selectable.
- Remaining future improvement: investigate/reduce the Qt/portal warning and possibly reduce memory footprint, but it is stable and no longer causing login instability.
