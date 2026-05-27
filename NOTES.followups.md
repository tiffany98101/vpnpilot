# Followups

- `src/vpnpilot/diagnostics.py`: keyword-driven redaction remains unsound long-term; durable secret safety needs allowlist-driven logging and diagnostics output.
- `scripts/install-protonvpn-cli-fedora.sh`: repository RPM SHA256 and GPG fingerprint pins currently cover Fedora 44 only; add pins for each supported Fedora release before using the helper there.
- `src/vpnpilot/controller.py:244`: ordinary NetworkManager link-flap, captive-portal-auth, and non-suspend connectivity changes still rely on periodic polling; the durable fix is an event-driven NetworkManager/logind DBus subscription layer that calls the existing force-refresh path without disconnecting active tunnels.
