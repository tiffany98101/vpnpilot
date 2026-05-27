#!/usr/bin/env bash
set -euo pipefail

RELEASE_RPM="protonvpn-stable-release-1.0.4-1.noarch.rpm"

# Update these pins only after downloading Proton's current release RPM and
# key from the official repository and verifying them out-of-band:
#   curl -fL -O "https://repo.protonvpn.com/fedora-44-stable/protonvpn-stable-release/${RELEASE_RPM}"
#   sha256sum "${RELEASE_RPM}"
#   curl -fL -o public_key.asc "https://repo.protonvpn.com/fedora-44-stable/public_key.asc"
#   gpg --show-keys --with-fingerprint public_key.asc
declare -A RELEASE_SHA256_BY_FEDORA=(
  ["44"]="c3a4ca5943b142997597c1e1248226cfafbabe914c89895e0a8b2890e422657c"
)
declare -A GPG_FINGERPRINT_BY_FEDORA=(
  ["44"]="6929133BDE1CE1CFA9EDB286D84176F6844830D4"
)

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

RELEASE_URL="https://repo.protonvpn.com/fedora-${FEDORA_VERSION}-stable/protonvpn-stable-release/${RELEASE_RPM}"
EXPECTED_RELEASE_SHA256="${RELEASE_SHA256_BY_FEDORA[${FEDORA_VERSION}]:-}"
EXPECTED_GPG_FINGERPRINT="${GPG_FINGERPRINT_BY_FEDORA[${FEDORA_VERSION}]:-}"
if [[ -z "${EXPECTED_RELEASE_SHA256}" || -z "${EXPECTED_GPG_FINGERPRINT}" ]]; then
  echo "No pinned Proton repository RPM hash/GPG fingerprint for Fedora ${FEDORA_VERSION}." >&2
  echo "Update RELEASE_SHA256_BY_FEDORA and GPG_FINGERPRINT_BY_FEDORA in this script first." >&2
  exit 1
fi
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

ACTUAL_RELEASE_SHA256="$(sha256sum "${RELEASE_RPM}" | awk '{print $1}')"
if [[ "${ACTUAL_RELEASE_SHA256}" != "${EXPECTED_RELEASE_SHA256}" ]]; then
  echo "Downloaded Proton repository RPM SHA256 mismatch." >&2
  echo "Expected: ${EXPECTED_RELEASE_SHA256}" >&2
  echo "Actual:   ${ACTUAL_RELEASE_SHA256}" >&2
  exit 1
fi

sudo dnf install "./${RELEASE_RPM}"
INSTALLED_VERSION="$(rpm -q --qf '%{VERSION} %{RELEASE}\n' protonvpn-stable-release)"
if [[ "${INSTALLED_VERSION}" != "1.0.4 1" ]]; then
  echo "Unexpected protonvpn-stable-release version: ${INSTALLED_VERSION}" >&2
  exit 1
fi

REPO_FILE="/etc/yum.repos.d/protonvpn-stable.repo"
if [[ ! -r "${REPO_FILE}" ]]; then
  echo "Expected repo file missing after install: ${REPO_FILE}" >&2
  exit 1
fi
GPG_KEY_URL="$(
  awk -F= '/^[[:space:]]*gpgkey[[:space:]]*=/{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' "${REPO_FILE}" |
    sed "s/\\\$releasever/${FEDORA_VERSION}/g"
)"
if [[ -z "${GPG_KEY_URL}" ]]; then
  echo "Could not find gpgkey URL in ${REPO_FILE}." >&2
  exit 1
fi
if command -v wget >/dev/null 2>&1; then
  wget -O public_key.asc "${GPG_KEY_URL}"
else
  curl -fL -o public_key.asc "${GPG_KEY_URL}"
fi
ACTUAL_GPG_FINGERPRINT="$(
  gpg --show-keys --with-colons --fingerprint public_key.asc |
    awk -F: '$1 == "fpr" {print toupper($10); exit}'
)"
if [[ "${ACTUAL_GPG_FINGERPRINT}" != "${EXPECTED_GPG_FINGERPRINT}" ]]; then
  echo "Proton repository GPG key fingerprint mismatch." >&2
  echo "Expected: ${EXPECTED_GPG_FINGERPRINT}" >&2
  echo "Actual:   ${ACTUAL_GPG_FINGERPRINT}" >&2
  exit 1
fi

sudo dnf check-update --refresh || [[ $? -eq 100 ]]
sudo dnf install proton-vpn-cli

echo
echo "Proton VPN CLI installed. Sign in separately with:"
echo "  protonvpn signin <email>"
