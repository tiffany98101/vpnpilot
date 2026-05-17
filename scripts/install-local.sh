#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor/scalable/apps"
METAINFO_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/metainfo"

echo "Installing VPNPilot desktop integration for this user"
echo "Project: ${ROOT}"

mkdir -p "${BIN_DIR}" "${APP_DIR}" "${ICON_DIR}" "${METAINFO_DIR}"

if [[ -e "${BIN_DIR}/vpnpilot" ]] && ! grep -q "VPNPilot local launcher" "${BIN_DIR}/vpnpilot"; then
  echo "Refusing to overwrite unrelated file: ${BIN_DIR}/vpnpilot" >&2
  exit 1
fi

echo "Installing launcher wrapper: ${BIN_DIR}/vpnpilot"
cat > "${BIN_DIR}/vpnpilot" <<EOF
#!/usr/bin/env bash
# VPNPilot local launcher
set -euo pipefail
cd "${ROOT}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  exec "${ROOT}/.venv/bin/python" -m vpnpilot "\$@"
fi
export PYTHONPATH="${ROOT}/src\${PYTHONPATH:+:\${PYTHONPATH}}"
exec python3 -m vpnpilot "\$@"
EOF
chmod 0755 "${BIN_DIR}/vpnpilot"

echo "Installing desktop file: ${APP_DIR}/vpnpilot.desktop"
install -m 0644 "${ROOT}/packaging/vpnpilot.desktop" "${APP_DIR}/vpnpilot.desktop"

echo "Installing icon: ${ICON_DIR}/vpnpilot.svg"
install -m 0644 "${ROOT}/packaging/icons/vpnpilot.svg" "${ICON_DIR}/vpnpilot.svg"

echo "Installing AppStream metadata: ${METAINFO_DIR}/vpnpilot.metainfo.xml"
install -m 0644 "${ROOT}/packaging/metainfo/vpnpilot.metainfo.xml" \
  "${METAINFO_DIR}/vpnpilot.metainfo.xml"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
fi

echo
echo "Done. Start VPNPilot from the KDE application launcher, or run:"
echo "  vpnpilot"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo
  echo "Note: ${BIN_DIR} is not currently on PATH in this shell."
fi
