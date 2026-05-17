# Releasing VPNPilot

1. Update the version in `pyproject.toml`, `src/vpnpilot/__init__.py`, and `packaging/vpnpilot.spec`.
2. Run the test suite:

   ```sh
   pytest -q
   ```

3. Build a local RPM if RPM tooling is available:

   ```sh
   ./scripts/build-rpm.sh
   ```

4. Commit the release changes.
5. Create and push a version tag:

   ```sh
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

6. GitHub Actions builds RPM and SRPM artifacts for the tag.
7. Tag builds attach the RPM and SRPM files to the GitHub Release for that tag.
8. Test the release on a clean Fedora/KDE machine or VM:
   - Install with `sudo dnf install ./vpnpilot-*.rpm`.
   - Verify the desktop launcher starts VPNPilot.
   - Verify Proton VPN CLI is installed automatically by dnf when the dependency is resolvable.
   - If Proton VPN CLI is not installed, verify VPNPilot shows a clear runtime message that names Proton VPN CLI and explains how to install it.
   - Sign in separately with `protonvpn signin <email>` using your own Proton VPN account.
