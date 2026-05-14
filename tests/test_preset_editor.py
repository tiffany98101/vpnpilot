"""Tests for the PresetEditorDialog."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog

from vpnpilot.catalog.models import CatalogEntry, City, CityFeature, Country, EntryState
from vpnpilot.preset import (
    Preset,
    PresetFlags,
    PresetTarget,
    TargetKind,
)
from vpnpilot.preset_editor import PresetEditorDialog


class FakeCatalog(QObject):
    """Minimal catalog double for preset editor tests."""

    catalog_changed = pyqtSignal(str)

    def __init__(self, countries: list[Country] | None = None) -> None:
        super().__init__()
        self._countries = countries
        self._entries: dict[str, CatalogEntry] = {}
        self.cities_calls: list[str] = []

    def countries_if_ready(self) -> list[Country] | None:
        return self._countries

    def cities_if_loaded(self, code: str):
        entry = self._entries.get(code.upper())
        if entry and entry.state is EntryState.LOADED:
            return entry.cities
        return None

    def entry_state(self, code: str) -> EntryState:
        entry = self._entries.get(code.upper())
        return entry.state if entry else EntryState.NOT_FETCHED

    def cities(self, code: str):
        self.cities_calls.append(code.upper())
        return self._entries.get(code.upper(), CatalogEntry(country_code=code.upper()))

    def set_entry_loaded(self, code: str, cities: list[City]) -> None:
        code = code.upper()
        self._entries[code] = CatalogEntry(
            country_code=code, state=EntryState.LOADED, cities=cities
        )


_COUNTRIES = [
    Country(code="DE", name="Germany"),
    Country(code="US", name="United States"),
]

_DE_CITIES = [
    City(name="Berlin", country_code="DE"),
    City(name="Frankfurt", country_code="DE", features=frozenset({CityFeature.P2P})),
]


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


# ---------------------------------------------------------------------------
# Catalog-backed combobox tests
# ---------------------------------------------------------------------------


def test_country_combobox_populated_from_catalog(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    dlg = PresetEditorDialog(preset=None, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    # Select COUNTRY kind.
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.COUNTRY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    dlg.show()
    # Combobox should have entries for each country.
    assert dlg.country_combo.count() == 2
    items = [dlg.country_combo.itemData(i) for i in range(dlg.country_combo.count())]
    assert "DE" in items
    assert "US" in items


def test_city_combobox_populates_when_country_selected(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", _DE_CITIES)
    dlg = PresetEditorDialog(preset=None, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    # Select CITY kind.
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.CITY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    dlg.show()
    # Select Germany in the country combobox.
    for i in range(dlg.country_combo.count()):
        if dlg.country_combo.itemData(i) == "DE":
            dlg.country_combo.setCurrentIndex(i)
            break
    # City combobox should now have DE cities.
    city_texts = [dlg.city_combo.itemText(i) for i in range(dlg.city_combo.count())]
    assert "Berlin" in city_texts
    assert "Frankfurt" in city_texts


def test_country_value_from_catalog_returns_code(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    dlg = PresetEditorDialog(preset=None, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.COUNTRY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    # Select Germany by userData.
    for i in range(dlg.country_combo.count()):
        if dlg.country_combo.itemData(i) == "DE":
            dlg.country_combo.setCurrentIndex(i)
            break
    assert dlg._current_value() == "DE"


def test_free_text_fallback_country(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    dlg = PresetEditorDialog(preset=None, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.COUNTRY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    # Type a free-text value not in the catalog.
    dlg.country_combo.setEditText("JP")
    assert dlg._current_value() == "JP"


def test_existing_preset_country_preselected(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    existing = Preset.new(
        name="Germany fastest",
        target=PresetTarget(kind=TargetKind.COUNTRY, value="DE"),
    )
    dlg = PresetEditorDialog(preset=existing, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    # The country combobox should have "DE" selected (by code lookup).
    assert dlg._current_value() == "DE"


def test_existing_preset_city_free_text_when_country_unknown(qapp_instance, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    existing = Preset.new(
        name="Seattle",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
    )
    dlg = PresetEditorDialog(preset=existing, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    # City combo should have "Seattle" as free-text.
    assert dlg.city_combo.currentText() == "Seattle"
    assert dlg._current_value() == "Seattle"


def test_catalog_changed_updates_country_combobox(qapp_instance, qtbot):
    cat = FakeCatalog(countries=None)
    dlg = PresetEditorDialog(preset=None, taken_names=set(), catalog=cat)
    qtbot.addWidget(dlg)
    for i in range(dlg.kind_combo.count()):
        if dlg.kind_combo.itemData(i) is TargetKind.COUNTRY:
            dlg.kind_combo.setCurrentIndex(i)
            break
    assert dlg.country_combo.count() == 0

    # Countries arrive via signal.
    cat._countries = _COUNTRIES
    dlg._on_catalog_changed("")

    assert dlg.country_combo.count() == 2
