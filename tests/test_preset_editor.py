"""Tests for the PresetEditorDialog."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from vpnpilot.preset import (
    Preset,
    PresetFlags,
    PresetTarget,
    TargetKind,
)
from vpnpilot.preset_editor import PresetEditorDialog


@pytest.fixture
def qapp_instance():
    return QApplication.instance() or QApplication([])


def test_new_mode_starts_empty(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    assert dlg.name_edit.text() == ""
    assert dlg._current_kind() is TargetKind.NONE
    assert dlg.modifier_none.isChecked() is True
    assert dlg.random_check.isChecked() is False


def test_edit_mode_prefills(qapp_instance, qtbot):
    existing = Preset.new(
        name="LA P2P",
        target=PresetTarget(kind=TargetKind.CITY, value="Los Angeles"),
        flags=PresetFlags(p2p=True, random=True),
    )
    dlg = PresetEditorDialog(preset=existing, taken_names=set())
    qtbot.addWidget(dlg)
    assert dlg.name_edit.text() == "LA P2P"
    assert dlg._current_kind() is TargetKind.CITY
    assert dlg.value_edit.text() == "Los Angeles"
    assert dlg.modifier_p2p.isChecked() is True
    assert dlg.random_check.isChecked() is True


def test_value_field_hidden_for_none_kind(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg.show()
    assert not dlg.value_edit.isVisible()


def test_value_field_visible_when_kind_changed(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg.show()
    # Pick CITY (index 2 in _KIND_ORDER).
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.CITY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    assert dlg.value_edit.isVisible()


def test_save_rejects_empty_name(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg._on_save()
    assert "empty" in dlg.error_label.text().lower()
    # Dialog stays open (not accepted yet).
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_save_rejects_duplicate_name(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names={"Seattle"})
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("Seattle")
    dlg._on_save()
    assert "already exists" in dlg.error_label.text().lower()


def test_save_rejects_missing_value_for_non_none_kind(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("NYC")
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.CITY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    dlg._on_save()
    assert "value" in dlg.error_label.text().lower()


def test_save_accepts_valid_input(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg.name_edit.setText("Random US P2P")
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.COUNTRY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    dlg.value_edit.setText("US")
    dlg.modifier_p2p.setChecked(True)
    dlg.random_check.setChecked(True)
    dlg._on_save()
    assert dlg.result() == QDialog.DialogCode.Accepted
    name, target, flags = dlg.values()
    assert name == "Random US P2P"
    assert target.kind is TargetKind.COUNTRY
    assert target.value == "US"
    assert flags.p2p is True
    assert flags.random is True


def test_modifiers_are_mutually_exclusive(qapp_instance, qtbot):
    dlg = PresetEditorDialog(preset=None, taken_names=set())
    qtbot.addWidget(dlg)
    dlg.modifier_p2p.setChecked(True)
    assert dlg.modifier_p2p.isChecked()
    dlg.modifier_tor.setChecked(True)  # exclusive group flips off the others
    assert not dlg.modifier_p2p.isChecked()
    assert dlg.modifier_tor.isChecked()


def test_editing_preset_excludes_own_name_from_taken_check(qapp_instance, qtbot):
    # If editing a preset named "Seattle" and "Seattle" is in taken_names
    # only because of itself, the caller should have filtered it out.
    # The dialog itself does not filter; verify it accepts a name that's
    # *not* in the passed set.
    existing = Preset.new(
        name="Seattle",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
    )
    dlg = PresetEditorDialog(preset=existing, taken_names=set())  # caller-filtered
    qtbot.addWidget(dlg)
    dlg._on_save()
    assert dlg.result() == QDialog.DialogCode.Accepted
