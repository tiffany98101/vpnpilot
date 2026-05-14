Name:           vpnpilot
Version:        0.5.0
Release:        1%{?dist}
Summary:        Unofficial tray UI for the ProtonVPN CLI

# vpnpilot is MIT. Bundled qasync is BSD-2-Clause.
License:        MIT AND BSD-2-Clause
# TODO: replace with canonical upstream project URL once publicly published.
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
* Thu May 14 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.5.0-1
- Server browser UI (BrowseTab): two-pane country/city view in main window,
  backed by ServerCatalog. Countries pane with text filter and state glyphs;
  cities pane with feature badges (P2P, Tor); server-ID escape hatch; Refresh
  button; signed-out degradation. Browse tab is secondary to the preset list.
- Preset editor graduation: Country and City fields become editable QComboBox
  backed by catalog data when catalog is available. Free-text fallback always
  allowed. Loading existing presets pre-selects by code; unknown values shown
  as free-text without data loss.
- Controller: connect_to_location(country_code, city=None) and
  connect_to_server_id(server_id) with server-ID regex validation.
- Catalog: non-triggering accessors (countries_if_ready, cities_if_loaded,
  entry_state, entry_error) for read-only UI access without side effects.

* Thu May 14 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.4.0-1
- In-memory server catalog (vpnpilot.catalog): lazy country + city fetch
  from the protonvpn CLI, background prewarm (sequential, one country at
  a time), catalog_changed Qt signal for future browser UI.
- vpnpilot catalog dump subcommand: JSON output for diagnostic use.
- ProtonCLI.run_command() generic entry point; countries_list() and
  cities_list() typed helpers.

* Wed May 13 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.2.1-1
- Single-instance lock (fcntl.flock on $XDG_RUNTIME_DIR/vpnpilot.lock).
  Prevents duplicate tray icons when launched twice.

* Wed May 13 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.2.0-1
- Sign-in detection: orthogonal AuthState axis on ConnectionInfo,
  signed-out tray icon, sign-in panel (modeless, Copy + auto-recheck),
  last-known email persisted to ~/.config/vpnpilot/state.json.

* Wed May 13 2026 Tiffany von Arnim <tiffany.vonarnim@gmail.com> - 0.1.0-1
- Initial RPM. Tray-only vertical slice.
