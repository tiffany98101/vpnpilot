"""Modal preset editor dialog — used for both New… and Edit… actions.

The dialog is storage-agnostic. The caller passes in an optional
existing Preset and the set of already-taken names; on accept it
returns the validated tuple (name, target, flags). Persisting goes
through PresetStore at the caller.

When a ServerCatalog is provided the Country and City fields become
editable comboboxes backed by catalog data. Free-text is always allowed
as a fallback — unknown values produce a soft warning, not a hard block.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .preset import Preset, PresetFlags, PresetTarget, TargetKind

log = logging.getLogger(__name__)

# Order shown in the combobox.
_KIND_ORDER = [
    (TargetKind.NONE, "Fastest available (no specific target)"),
    (TargetKind.COUNTRY, "Country"),
    (TargetKind.CITY, "City"),
    (TargetKind.SERVER_ID, "Specific server"),
]

# Hint text per kind, shown next to (or under) the value field.
_KIND_HINT = {
    TargetKind.NONE: "",
    TargetKind.COUNTRY: "Country code (US, GB, DE) or full name (\"United States\").",
    TargetKind.CITY: "City name. Multi-word names should be entered as written.",
    TargetKind.SERVER_ID: "Server ID like \"US-WA#187\" or \"US-GA#29-TOR\".",
}

_SIGNED_OUT_HINT = "Sign in to enable catalog picker."
_LOADING_HINT = "Loading catalog…"


class PresetEditorDialog(QDialog):
    """Edit one preset.

    Pass `preset=None` for New mode; `preset=<Preset>` for Edit mode.
    `taken_names` should be the set of names currently in the store,
    minus the editing preset's own name if applicable.
    `catalog` is optional; when provided Country and City fields become
    editable comboboxes.
    """

    def __init__(
        self,
        *,
        preset: Preset | None = None,
        taken_names: set[str],
        catalog=None,  # ServerCatalog | None — avoid circular import at module level
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit preset" if preset else "New preset")
        self.setModal(True)
        self._taken_names = set(taken_names)
        self._editing_preset = preset
        self._catalog = catalog

        outer = QVBoxLayout(self)

        form = QFormLayout()
        outer.addLayout(form)

        # Name -------------------------------------------------------
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("nameEdit")
        form.addRow("Name:", self.name_edit)

        # Target kind ------------------------------------------------
        self.kind_combo = QComboBox()
        self.kind_combo.setObjectName("kindCombo")
        for kind, label in _KIND_ORDER:
            self.kind_combo.addItem(label, userData=kind)
        form.addRow("Target:", self.kind_combo)

        # Target value -----------------------------------------------
        # Country and City kinds get catalog-backed editable comboboxes;
        # SERVER_ID and NONE get a plain QLineEdit (value_edit).
        self.value_edit = QLineEdit()
        self.value_edit.setObjectName("valueEdit")
        self.value_label = QLabel("Value:")

        self.country_combo = QComboBox()
        self.country_combo.setObjectName("countryCombo")
        self.country_combo.setEditable(True)
        self.country_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.country_label = QLabel("Country:")

        self.city_combo = QComboBox()
        self.city_combo.setObjectName("cityCombo")
        self.city_combo.setEditable(True)
        self.city_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.city_label = QLabel("City:")

        # All three value widgets share the same form row (only one visible).
        form.addRow(self.value_label, self.value_edit)
        form.addRow(self.country_label, self.country_combo)
        form.addRow(self.city_label, self.city_combo)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setStyleSheet("color: #777; font-size: 9pt;")
        self.hint_label.setWordWrap(True)
        outer.addWidget(self.hint_label)

        # Flags ------------------------------------------------------
        flags_box = QGroupBox("Options")
        flags_layout = QVBoxLayout(flags_box)

        modifier_label = QLabel("Server type (at most one):")
        modifier_label.setStyleSheet("font-weight: 600;")
        flags_layout.addWidget(modifier_label)

        self._modifier_group = QButtonGroup(self)
        self._modifier_group.setExclusive(True)
        self.modifier_none = QRadioButton("Any")
        self.modifier_none.setObjectName("modifierNone")
        self.modifier_p2p = QRadioButton("P2P-optimized")
        self.modifier_p2p.setObjectName("modifierP2P")
        self.modifier_secure_core = QRadioButton("Secure Core")
        self.modifier_secure_core.setObjectName("modifierSecureCore")
        self.modifier_tor = QRadioButton("Tor over VPN")
        self.modifier_tor.setObjectName("modifierTor")
        for btn in (
            self.modifier_none,
            self.modifier_p2p,
            self.modifier_secure_core,
            self.modifier_tor,
        ):
            self._modifier_group.addButton(btn)
            flags_layout.addWidget(btn)

        flags_layout.addSpacing(6)
        self.random_check = QCheckBox("Pick a random matching server (--random)")
        self.random_check.setObjectName("randomCheck")
        flags_layout.addWidget(self.random_check)
        outer.addWidget(flags_box)

        # Error display ---------------------------------------------
        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setStyleSheet("color: #c00;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        outer.addWidget(self.error_label)

        # Buttons ---------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.save_button.setObjectName("saveButton")
        self.cancel_button.setObjectName("cancelButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Signals
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.country_combo.currentIndexChanged.connect(self._on_country_selection_changed)

        # Populate catalog data if available.
        if catalog is not None:
            self._populate_catalog_countries()
            if catalog is not None:
                catalog.catalog_changed.connect(self._on_catalog_changed)

        self._populate_from_preset(preset)
        self._on_kind_changed()  # set initial value-field visibility

    # ----- public -----

    def values(self) -> tuple[str, PresetTarget, PresetFlags]:
        """Return the user's choices. Only meaningful after exec() == Accepted."""
        kind = self._current_kind()
        value = self._current_value()
        return (
            self.name_edit.text().strip(),
            PresetTarget(kind=kind, value=value),
            PresetFlags(
                p2p=self.modifier_p2p.isChecked(),
                secure_core=self.modifier_secure_core.isChecked(),
                tor=self.modifier_tor.isChecked(),
                random=self.random_check.isChecked(),
            ),
        )

    # ----- catalog wiring -----

    def _populate_catalog_countries(self) -> None:
        if self._catalog is None:
            return
        countries = self._catalog.countries_if_ready()
        if countries is None:
            self.country_combo.setPlaceholderText(_LOADING_HINT)
            return
        self.country_combo.clear()
        for c in sorted(countries, key=lambda x: x.name):
            self.country_combo.addItem(f"{c.name}  ({c.code})", userData=c.code)

    def _populate_catalog_cities(self, country_code: str) -> None:
        self.city_combo.clear()
        if self._catalog is None or not country_code:
            return
        cities = self._catalog.cities_if_loaded(country_code)
        if cities is None:
            self.city_combo.setPlaceholderText("Loading cities…")
            # Kick off a fetch; signal will update us when done.
            self._catalog.cities(country_code)
            return
        for city in sorted(cities, key=lambda c: c.name):
            self.city_combo.addItem(city.name)

    def _on_catalog_changed(self, country_code: str) -> None:
        kind = self._current_kind()
        if country_code == "" and kind is TargetKind.COUNTRY:
            # Countries list loaded; repopulate country combobox.
            current_text = self.country_combo.currentText()
            self._populate_catalog_countries()
            if current_text:
                self._try_select_in_combo(self.country_combo, current_text)
        elif country_code and kind is TargetKind.CITY:
            # Cities for a country loaded; update city combobox if relevant.
            selected_code = self._resolved_country_code()
            if selected_code and country_code.upper() == selected_code.upper():
                current_text = self.city_combo.currentText()
                self._populate_catalog_cities(selected_code)
                if current_text:
                    self._try_select_in_combo(self.city_combo, current_text)

    def _on_country_selection_changed(self, index: int) -> None:
        if self._current_kind() is not TargetKind.CITY:
            return
        code = self._resolved_country_code()
        if code:
            self._populate_catalog_cities(code)

    def _resolved_country_code(self) -> str:
        """Return the country code for the current country combobox text.

        Prefers the stored userData (catalog entries); falls back to treating
        the text as a bare country code.
        """
        idx = self.country_combo.currentIndex()
        if idx >= 0:
            code = self.country_combo.itemData(idx)
            if code:
                return str(code)
        text = self.country_combo.currentText().strip()
        # If the user typed something like "United States  (US)", extract the code.
        if "(" in text and ")" in text:
            start = text.rfind("(")
            end = text.rfind(")")
            if start < end:
                return text[start + 1:end].strip()
        return text.split()[0].upper() if text else ""

    @staticmethod
    def _try_select_in_combo(combo: QComboBox, text: str) -> None:
        """Select `text` in the combo if it exists; otherwise leave as-is."""
        idx = combo.findText(text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(text)

    # ----- internals -----

    def _select_country_by_code(self, code: str) -> bool:
        """Select the country combo item whose userData matches `code`."""
        for i in range(self.country_combo.count()):
            if self.country_combo.itemData(i) == code:
                self.country_combo.setCurrentIndex(i)
                return True
        return False

    def _populate_from_preset(self, preset: Preset | None) -> None:
        if preset is None:
            self.modifier_none.setChecked(True)
            self._select_kind(TargetKind.NONE)
            return
        self.name_edit.setText(preset.name)
        self._select_kind(preset.target.kind)
        value = preset.target.value

        if preset.target.kind is TargetKind.COUNTRY and self._catalog is not None:
            # Try to select by code first (countries are stored as codes),
            # then by text (full name), then fall back to free-text.
            if not self._select_country_by_code(value):
                self._try_select_in_combo(self.country_combo, value)
                if self.country_combo.currentText() != value:
                    self.country_combo.setEditText(value)
        elif preset.target.kind is TargetKind.CITY and self._catalog is not None:
            # City presets store only the city name; put it in the city combo.
            # Country combo stays unset — user can select for discovery.
            self.city_combo.setEditText(value)
        else:
            self.value_edit.setText(value)

        if preset.flags.p2p:
            self.modifier_p2p.setChecked(True)
        elif preset.flags.secure_core:
            self.modifier_secure_core.setChecked(True)
        elif preset.flags.tor:
            self.modifier_tor.setChecked(True)
        else:
            self.modifier_none.setChecked(True)
        self.random_check.setChecked(preset.flags.random)

    def _select_kind(self, kind: TargetKind) -> None:
        for i, (k, _) in enumerate(_KIND_ORDER):
            if k is kind:
                self.kind_combo.setCurrentIndex(i)
                return

    def _current_kind(self) -> TargetKind:
        return self.kind_combo.currentData()

    def _current_value(self) -> str:
        kind = self._current_kind()
        if kind is TargetKind.NONE:
            return ""
        if kind is TargetKind.COUNTRY and self._catalog is not None:
            # Only use the catalog userData when the displayed text actually
            # matches the selected item — if the user typed something else,
            # treat it as free-text.
            idx = self.country_combo.currentIndex()
            if idx >= 0:
                data = self.country_combo.itemData(idx)
                if data and self.country_combo.currentText() == self.country_combo.itemText(idx):
                    return str(data)
            text = self.country_combo.currentText().strip()
            # Strip trailing "(CODE)" annotation if present (typed by user).
            if "(" in text and ")" in text:
                start = text.rfind("(")
                end = text.rfind(")")
                if start < end:
                    return text[start + 1:end].strip()
            return text
        if kind is TargetKind.CITY and self._catalog is not None:
            return self.city_combo.currentText().strip()
        return self.value_edit.text().strip()

    def _on_kind_changed(self) -> None:
        kind = self._current_kind()
        use_catalog = self._catalog is not None

        # Hide all value widgets first.
        self.value_edit.setVisible(False)
        self.value_label.setVisible(False)
        self.country_combo.setVisible(False)
        self.country_label.setVisible(False)
        self.city_combo.setVisible(False)
        self.city_label.setVisible(False)

        if kind is TargetKind.NONE:
            pass  # nothing to show
        elif kind is TargetKind.COUNTRY:
            if use_catalog:
                self.country_combo.setVisible(True)
                self.country_label.setVisible(True)
            else:
                self.value_edit.setVisible(True)
                self.value_label.setVisible(True)
        elif kind is TargetKind.CITY:
            if use_catalog:
                self.country_combo.setVisible(True)
                self.country_label.setVisible(True)
                self.city_combo.setVisible(True)
                self.city_label.setVisible(True)
                # Populate cities for current country selection.
                code = self._resolved_country_code()
                if code:
                    self._populate_catalog_cities(code)
            else:
                self.value_edit.setVisible(True)
                self.value_label.setVisible(True)
        else:
            self.value_edit.setVisible(True)
            self.value_label.setVisible(True)

        hint = _KIND_HINT.get(kind, "")
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self._show_error("Name must not be empty.")
            return
        if name in self._taken_names:
            self._show_error(f"A preset named \"{name}\" already exists.")
            return
        kind = self._current_kind()
        value = self._current_value()
        if kind is not TargetKind.NONE and not value:
            self._show_error("Target value is required for this kind.")
            return
        # PresetFlags will raise if more than one modifier is set, but
        # the exclusive QButtonGroup prevents that — leave the
        # construction in values().
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setVisible(True)
