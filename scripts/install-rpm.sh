#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

shopt -s nullglob
rpms=(dist/rpm/vpnpilot-*.rpm)
shopt -u nullglob

if (( ${#rpms[@]} == 0 )); then
  echo "Prebuilt VPNPilot RPM is missing from dist/rpm." >&2
  echo "Expected exactly one file matching: dist/rpm/vpnpilot-*.rpm" >&2
  exit 1
fi

if (( ${#rpms[@]} > 1 )); then
  echo "Found multiple VPNPilot RPMs in dist/rpm; refusing to choose one:" >&2
  printf '  %s\n' "${rpms[@]}" >&2
  exit 1
fi

sudo dnf install "./${rpms[0]}"
