#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

missing=()
for tool in git rpmspec rpmbuild desktop-file-validate; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    missing+=("${tool}")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "Missing required RPM build tool(s): ${missing[*]}" >&2
  echo "On Fedora, install them with:" >&2
  echo "  sudo dnf install git rpm-build rpmdevtools python3-devel pyproject-rpm-macros desktop-file-utils python3-pyqt6" >&2
  exit 1
fi

if ! rpm --showrc > /tmp/vpnpilot-rpm-showrc.$$ || ! grep -q 'pyproject_wheel' /tmp/vpnpilot-rpm-showrc.$$; then
  rm -f /tmp/vpnpilot-rpm-showrc.$$
  echo "Missing pyproject RPM macros." >&2
  echo "On Fedora, install them with:" >&2
  echo "  sudo dnf install pyproject-rpm-macros" >&2
  exit 1
fi
rm -f /tmp/vpnpilot-rpm-showrc.$$

SPEC="${ROOT}/packaging/vpnpilot.spec"
NAME="$(rpmspec -q --srpm --queryformat '%{NAME}\n' "${SPEC}" | head -n1)"
VERSION="$(rpmspec -q --srpm --queryformat '%{VERSION}\n' "${SPEC}" | head -n1)"
TARBALL="${HOME}/rpmbuild/SOURCES/${NAME}-${VERSION}.tar.gz"

mkdir -p "${HOME}/rpmbuild"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

echo "Building ${NAME}-${VERSION} RPM from git archive"
git archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${TARBALL}" HEAD

rpmbuild -ba "${SPEC}"

echo
echo "Built RPM artifacts:"
find "${HOME}/rpmbuild/RPMS" "${HOME}/rpmbuild/SRPMS" \
  -type f \( -name "${NAME}-${VERSION}-*.rpm" -o -name "${NAME}-${VERSION}-*.src.rpm" \) \
  -print | sort
