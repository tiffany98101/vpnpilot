Name:           vpnpilot
Version:        0.1.0
Release:        1%{?dist}
Summary:        Unofficial tray UI for the ProtonVPN CLI

# vpnpilot is MIT. Bundled qasync is BSD-2-Clause.
License:        MIT AND BSD-2-Clause
URL:            https://example.invalid/vpnpilot
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
# Additional Python build requirements (setuptools, wheel, etc.) are
# generated dynamically by %generate_buildrequires from pyproject.toml's
# [build-system] requires.

# The CLI we wrap. The whole point of the app.
Requires:       proton-vpn-cli
# Qt UI dependency (only one of vpnpilot's runtime deps that is in
# Fedora repos; pydantic isn't used yet, qasync is bundled).
Requires:       python3-pyqt6
# A working system tray is needed. On KDE Plasma it's built-in; on
# GNOME the user installs the AppIndicator extension. We don't try
# to require an extension from RPM.
Recommends:     gnome-shell-extension-appindicator

%description
vpnpilot is an unofficial third-party tray indicator and helper for
managing a Proton VPN connection on Linux. It wraps the official
proton-vpn-cli — it does not reimplement the VPN protocol and never
runs with elevated privileges.

This is not affiliated with or endorsed by Proton AG. The Proton VPN
trademark and logos are not used.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files vpnpilot

# Desktop file
install -Dm0644 packaging/vpnpilot.desktop \
    %{buildroot}%{_datadir}/applications/vpnpilot.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/vpnpilot.desktop

# Application icon (scalable hicolor)
install -Dm0644 src/vpnpilot/resources/icon-app.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/vpnpilot.svg

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/vpnpilot
%{_datadir}/applications/vpnpilot.desktop
%{_datadir}/icons/hicolor/scalable/apps/vpnpilot.svg

%changelog
* Wed May 13 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.1.0-1
- Initial RPM. Tray-only vertical slice.
