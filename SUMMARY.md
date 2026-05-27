# VPNPilot Security Fix Summary

- 1. Singleton lock unsafe acquisition: `e3db8a5`, tests delta `+3`.
- 2. NetworkManager ownership semantics: `490ca37`, tests delta `+4`.
- 3. Escaped nmcli terse fields: `64a3676`, tests delta `+6`.
- 4. Private log file modes: `5ca8271`, tests delta `+1`.
- 5. Wrapped resolvectl DNS servers: `92a20e1`, tests delta `+2`.
- 6. Diagnostic redaction gaps: `70a757a`, tests delta `+13`.
- 7. Comma-containing Proton status cities: `e876f0a`, tests delta `+4`.
- 8. Proton repository install trust: `7ffc345`, tests delta `+0`.
- 9. CI action pinning and permissions: `e58ce25`, tests delta `+0`.
- 10. Catalog delimiter parsing: `854a6bb`, tests delta `+3`.

## Decisions

- NetworkManager ownership uses the configured profile UUID when present, plus strict `.protonvpn.tcp` and `.protonvpn.udp` imported-profile names, because current documented manual imports produce those names and there is no VPNPilot-owned importer yet.
- Unrelated active NetworkManager VPNs use a new `external_vpn_active` state instead of `disconnected`, because the UI needs to distinguish outside protection from VPNPilot-controlled protection.
- The log handler exposes test-only size knobs through `configure_logging()` rather than a separate private entrypoint, because rollover behavior has to be exercised through the real handler path.
- Proton repository trust pins are populated for Fedora 44 only, because those were the values verified during this run; other Fedora versions now abort until their pins are explicitly added.
- The Proton release RPM does not install a key under `/etc/pki/rpm-gpg/`, so the install helper reads the installed repo file's `gpgkey` URL, downloads that key, and compares its fingerprint to the pinned Fedora-version constant before refreshing the repository.
- Catalog city rows with unknown feature text are preserved as names instead of partially parsed, because provider-controlled delimiter text is ambiguous and the safer behavior is not to guess.

## Followups

- `src/vpnpilot/diagnostics.py`: keyword-driven redaction remains unsound long-term; durable secret safety needs allowlist-driven logging and diagnostics output.
- `scripts/install-protonvpn-cli-fedora.sh`: repository RPM SHA256 and GPG fingerprint pins currently cover Fedora 44 only; add pins for each supported Fedora release before using the helper there.
- Original review note: the allowlist-driven logging/redaction refactor is intentionally not part of this run.
- Original review note: operational/laptop-mobility axes are intentionally not part of this run and need a separate adversarial pass: suspend/resume, captive portal, link flap, IPv6-only links, and system clock jumps.

pytest: 353 passed
