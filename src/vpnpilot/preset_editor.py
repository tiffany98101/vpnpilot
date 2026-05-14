"""Modal preset editor dialog — used for both New… and Edit… actions.

The dialog is storage-agnostic. The caller passes in an optional
existing Preset and the set of already-taken names; on accept it
returns the validated tuple (name, target, flags). Persisting goes
through PresetStore at the caller.
"""

from __future__ import annotations

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


class PresetEditorDialog(QDialog):
    """Edit one preset.

    Pass `preset=None` for New mode; `preset=<Preset>` for Edit mode.
    `taken_names` should be the set of names currently in the store,
    minus the editing preset's own name if applicable.
    """

    def __init__(
        self,
        *,
        preset: Preset | None = None,
        taken_names: set[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit preset" if preset else "New preset")
        self.setModal(True)
        self._taken_names = set(taken_names)
        self._editing_preset = preset

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
        self.value_edit = QLineEdit()
        self.value_edit.setObjectName("valueEdit")
        self.value_label = QLabel("Value:")
        form.addRow(self.value_label, self.value_edit)

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

        self._populate_from_preset(preset)
        self._on_kind_changed()  # set initial value-field visibility

    # ----- public -----

    def values(self) -> tuple[str, PresetTarget, PresetFlags]:
        """Return the user's choices. Only meaningful after exec() == Accepted."""
        return (
            self.name_edit.text().strip(),
            PresetTarget(kind=self._current_kind(), value=self.value_edit.text().strip()),
            PresetFlags(
                p2p=self.modifier_p2p.isChecked(),
                secure_core=self.modifier_secure_core.isChecked(),
                tor=self.modifier_tor.isChecked(),
                random=self.random_check.isChecked(),
            ),
        )

    # ----- internals -----

    def _populate_from_preset(self, preset: Preset | None) -> None:
        if preset is None:
            self.modifier_none.setChecked(True)
            self._select_kind(TargetKind.NONE)
            return
        self.name_edit.setText(preset.name)
        self._select_kind(preset.target.kind)
        self.value_edit.setText(preset.target.value)
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

    def _on_kind_changed(self) -> None:
        kind = self._current_kind()
        # Value field is irrelevant when target is NONE.
        if kind is TargetKind.NONE:
            self.value_edit.setVisible(False)
            self.value_edit.clear()
            self.value_label.setVisible(False)
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
            self._show_error(f"A preset named “{name}” already exists.")
            return
        kind = self._current_kind()
        value = self.value_edit.text().strip()
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
