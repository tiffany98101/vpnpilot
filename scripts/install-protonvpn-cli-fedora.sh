#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify this operating system." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ "${ID:-}" != "fedora" && " ${ID_LIKE:-} " != *" fedora "* && " ${ID_LIKE:-} " != *" rhel "* ]]; then
  echo "This helper only supports Fedora/RPM-based systems." >&2
  exit 1
fi

if ! command -v dnf >/dev/null 2>&1; then
  echo "dnf is required to install Proton VPN CLI on Fedora." >&2
  exit 1
fi

if command -v protonvpn >/dev/null 2>&1; then
  echo "Proton VPN CLI is already installed: $(command -v protonvpn)"
  exit 0
fi

FEDORA_VERSION="${VERSION_ID:-}"
if [[ -z "${FEDORA_VERSION}" ]]; then
  echo "Cannot determine Fedora version from /etc/os-release." >&2
  exit 1
fi

RELEASE_RPM="protonvpn-stable-release-1.0.4-1.noarch.rpm"
RELEASE_URL="https://repo.protonvpn.com/fedora-${FEDORA_VERSION}-stable/protonvpn-stable-release/${RELEASE_RPM}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

echo "This will install Proton's official Fedora repository package, refresh dnf,"
echo "and install the official Proton VPN Linux CLI package: proton-vpn-cli."
echo "VPNPilot will not ask for or configure Proton VPN credentials."
echo

if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
  echo "wget or curl is required to download Proton's repository package." >&2
  echo "Install one first, for example: sudo dnf install wget" >&2
  exit 1
fi

cd "${TMPDIR}"
if command -v wget >/dev/null 2>&1; then
  wget "${RELEASE_URL}"
else
  curl -fL -O "${RELEASE_URL}"
fi

sudo dnf install "./${RELEASE_RPM}"
sudo dnf check-update --refresh || [[ $? -eq 100 ]]
sudo dnf install proton-vpn-cli

echo
echo "Proton VPN CLI installed. Sign in separately with:"
echo "  protonvpn signin <email>"
