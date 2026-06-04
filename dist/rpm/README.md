# Prebuilt RPM

This directory contains the prebuilt VPNPilot RPM for normal git-clone installs.

Normal users can install it from the repository root with:

```sh
sudo dnf install ./dist/rpm/vpnpilot-*.rpm
```

Maintainers update the RPM in this directory when cutting a new release.

For a local review build from the current working tree, run `make rpm` from the
repository root. That produces an installable RPM in `dist/` without replacing
the prebuilt RPM in this directory.
