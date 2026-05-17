#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
APP_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor/scalable/apps"
METAINFO_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/metainfo"

echo "Removing VPNPilot desktop integration for this user"

if [[ -f "${BIN_DIR}/vpnpilot" ]] && grep -q "VPNPilot local launcher" "${BIN_DIR}/vpnpilot"; then
  rm -f "${BIN_DIR}/vpnpilot"
fi

if [[ -f "${APP_DIR}/vpnpilot.desktop" ]] && grep -q "X-VPNPilot-Managed=true" \
  "${APP_DIR}/vpnpilot.desktop"; then
  rm -f "${APP_DIR}/vpnpilot.desktop"
fi

rm -f "${ICON_DIR}/vpnpilot.svg"
rm -f "${METAINFO_DIR}/vpnpilot.metainfo.xml"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
fi

echo "Done. User config and logs were left in place."
