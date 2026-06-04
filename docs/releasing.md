# Releasing VPNPilot

1. Update the version in `pyproject.toml`, `src/vpnpilot/__init__.py`, and `packaging/vpnpilot.spec`.
2. Run validation from the development environment:

   ```sh
   QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
   .venv/bin/ruff check src tests
   git diff --name-only -- '*.py' | xargs -r .venv/bin/ruff format --check
   git diff --check
   ```

3. Build a local review RPM if RPM tooling is available:

   ```sh
   make rpm
   ```

   `make rpm` archives tracked files from the current working tree, so it is the right path for pre-commit review builds. It writes the binary RPM to `dist/` and uses `.rpmbuild/` as repo-local scratch.

4. Build the CI/release RPM and SRPM from committed release content:

   ```sh
   ./scripts/build-rpm.sh
   ```

   This helper uses the committed repository content and writes artifacts under `~/rpmbuild/`.

5. Commit the release changes.
6. Create and push a version tag:

   ```sh
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

7. GitHub Actions builds RPM and SRPM artifacts for the tag.
8. Tag builds attach the RPM and SRPM files to the GitHub Release for that tag.
9. Test the release on a clean Fedora/KDE machine or VM:
   - Install with `sudo dnf install ./vpnpilot-*.rpm`.
   - Verify the desktop launcher starts VPNPilot.
   - Verify Proton VPN CLI is installed automatically by dnf when the dependency is resolvable.
   - If Proton VPN CLI is not installed, verify VPNPilot shows a clear runtime message that names Proton VPN CLI and explains how to install it.
   - Sign in separately with `protonvpn signin <email>` using your own Proton VPN account.
   - Open the main window, click `Sync Servers`, and verify it refreshes Browse catalog state without changing the active VPN connection.
