from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_desktop_file_has_expected_launcher_fields():
    text = _read("packaging/vpnpilot.desktop")

    assert "[Desktop Entry]" in text
    assert "Name=VPNPilot" in text
    assert "Comment=Proton VPN tray controller for Fedora KDE" in text
    assert "Exec=vpnpilot" in text
    assert "Icon=vpnpilot" in text
    assert "Categories=Network;Utility;Qt;" in text
    assert "X-VPNPilot-Managed=true" in text
    assert "/home/" not in text


def test_packaging_icon_exists_and_is_svg():
    icon = ROOT / "packaging/icons/vpnpilot.svg"

    assert icon.exists()
    assert "<svg" in icon.read_text(encoding="utf-8")


def test_appstream_metadata_exists():
    text = _read("packaging/metainfo/vpnpilot.metainfo.xml")

    assert "<id>vpnpilot.desktop</id>" in text
    assert "<name>VPNPilot</name>" in text
    assert "not affiliated" in text


def test_local_install_scripts_are_executable_and_guard_overwrites():
    install = ROOT / "scripts/install-local.sh"
    uninstall = ROOT / "scripts/uninstall-local.sh"
    install_text = install.read_text(encoding="utf-8")
    uninstall_text = uninstall.read_text(encoding="utf-8")

    assert os.access(install, os.X_OK)
    assert os.access(uninstall, os.X_OK)
    assert "Refusing to overwrite unrelated file" in install_text
    assert "VPNPilot local launcher" in install_text
    assert "PYTHONPATH" in install_text
    assert "VPNPilot local launcher" in uninstall_text
    assert "sudo" not in install_text
    assert "sudo" not in uninstall_text


def test_rpm_spec_installs_desktop_icon_and_metainfo():
    text = _read("packaging/vpnpilot.spec")

    assert (ROOT / "packaging/vpnpilot.spec").exists()
    assert "packaging/vpnpilot.desktop" in text
    assert "packaging/icons/vpnpilot.svg" in text
    assert "packaging/metainfo/vpnpilot.metainfo.xml" in text
    assert "python3-pyqt6" in text
    assert "proton-vpn-cli" in text
    assert "Requires:       proton-vpn-cli" in text
    assert "BuildArch:      noarch" in text
    assert "%{_bindir}/vpnpilot" in text
    assert (ROOT / "packaging/vpnpilot.desktop").exists()
    assert (ROOT / "packaging/icons/vpnpilot.svg").exists()
    assert (ROOT / "packaging/metainfo/vpnpilot.metainfo.xml").exists()


def test_rpm_build_script_exists_and_is_executable():
    script = ROOT / "scripts/build-rpm.sh"
    text = script.read_text(encoding="utf-8")

    assert script.exists()
    assert os.access(script, os.X_OK)
    assert "set -euo pipefail" in text
    assert "rpmspec" in text
    assert "git archive" in text
    assert "rpmbuild -ba" in text
    assert "dist/rpm/" in text


def test_prebuilt_rpm_exists_for_clone_installs():
    rpm_dir = ROOT / "dist/rpm"
    rpms = sorted(rpm_dir.glob("vpnpilot-*.rpm"))

    assert rpm_dir.exists()
    assert rpms


def test_rpm_install_script_installs_prebuilt_rpm_only():
    script = ROOT / "scripts/install-rpm.sh"
    text = script.read_text(encoding="utf-8")

    assert script.exists()
    assert os.access(script, os.X_OK)
    assert "set -euo pipefail" in text
    assert "dist/rpm/vpnpilot-*.rpm" in text
    assert "sudo dnf install" in text
    assert "rpmbuild" not in text
    assert not any(
        line.strip().startswith(("pip ", "python -m pip", "python3 -m pip"))
        for line in text.splitlines()
    )


def test_protonvpn_cli_fedora_helper_is_safe_and_executable():
    script = ROOT / "scripts/install-protonvpn-cli-fedora.sh"
    text = script.read_text(encoding="utf-8")

    assert script.exists()
    assert os.access(script, os.X_OK)
    assert "set -euo pipefail" in text
    assert "/etc/os-release" in text
    assert "protonvpn-stable-release" in text
    assert "sudo dnf install proton-vpn-cli" in text
    assert "credentials" in text


def test_github_actions_rpm_workflow_builds_and_publishes():
    workflow = ROOT / ".github/workflows/build-rpm.yml"
    text = workflow.read_text(encoding="utf-8")

    assert workflow.exists()
    assert "fedora:latest" in text
    assert "pytest -q" in text
    assert "./scripts/build-rpm.sh" in text
    assert "actions/upload-artifact" in text
    assert "vpnpilot-rpms" in text
    assert "softprops/action-gh-release" in text
    assert "refs/tags/v" in text


def test_readme_mentions_local_install_logs_and_diagnostics():
    text = _read("README.md")

    assert "sudo dnf install ./dist/rpm/vpnpilot-*.rpm" in text
    assert "./scripts/install-rpm.sh" in text
    assert "scripts/install-local.sh" in text
    assert "scripts/uninstall-local.sh" in text
    assert "~/.local/state/vpnpilot/vpnpilot.log" in text
    assert "Copy Diagnostic Info" in text
    assert "does not use Proton branding" in text
