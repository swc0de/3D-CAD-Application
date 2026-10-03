"""Entity Info: details of the selection, with editable name, material, tag and flags."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Edge, Entity, Face
from pymodeler.core.tags import UNTAGGED
from pymodeler.core.units import format_length, from_mm
from pymodeler.ops.organize import paint
from pymodeler.ui.panels.base import Panel

DEFAULT = "Default"


class EntityInfoPanel(Panel):
    title = "Entity Info"

    def __init__(self, document, viewport, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(document, viewport, parent)
        self.summary = QLabel("Nothing selected")
        self.summary.setWordWrap(True)
        self.form_widget = QWidget()
        self.form = QFormLayout(self.form_widget)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._name_changed)
        self.definition_label = QLabel()
        self.measure_label = QLabel()
        self.material_combo = QComboBox()
        self.material_combo.activated.connect(self._material_changed)
        self.tag_combo = QComboBox()
        self.tag_combo.activated.connect(self._tag_changed)
        self.hidden_check = QCheckBox("Hidden")
        self.hidden_check.clicked.connect(self._hidden_changed)
        self.soft_check = QCheckBox("Soft / smooth")
        self.soft_check.clicked.connect(self._soft_changed)
        for label, widget in (("Name", self.name_edit), ("Definition", self.definition_label),
                              ("Size", self.measure_label), ("Material", self.material_combo),
                              ("Tag", self.tag_combo)):
            self.form.addRow(label, widget)
        self.form.addRow(self.hidden_check)
        self.form.addRow(self.soft_check)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.form_widget)
        layout.addStretch(1)
        self.refresh()

    def items(self) -> list[Entity]:
        return self.viewport.selection.items()

    def refresh(self) -> None:
        self.selection_changed()

    def selection_changed(self) -> None:
        self._refreshing = True
        items = self.items()
        self.summary.setText(self.viewport.selection.describe())
        self.form_widget.setVisible(bool(items))
        model = self.document.model
        units = model.units
        single = items[0] if len(items) == 1 else None
        is_inst = isinstance(single, ComponentInstance)
        self._row_visible(self.name_edit, is_inst)
        self._row_visible(self.definition_label, is_inst and not single.is_group)  # type: ignore[union-attr]
        if is_inst:
            self.name_edit.setText(single.name)  # type: ignore[union-attr]
            self.definition_label.setText(
                f"{single.definition.name} ({len(single.definition.instances)} in model)")  # type: ignore[union-attr]
        measure = ""
        if isinstance(single, Face):
            measure = f"Area {self._area(single.area(), units)}"
        elif isinstance(single, Edge):
            measure = f"Length {format_length(single.length(), units)}"
        elif items:
            measure = f"{len(items)} entities"
        self.measure_label.setText(measure)
        paintable = [e for e in items if isinstance(e, (Face, ComponentInstance))]
        self._row_visible(self.material_combo, bool(paintable))
        self.material_combo.clear()
        self.material_combo.addItems([DEFAULT, *sorted(model.materials)])
        if paintable:
            current = paintable[0].material or DEFAULT
            self.material_combo.setCurrentText(current)
        self.tag_combo.clear()
        self.tag_combo.addItems(list(model.tags))
        if items:
            self.tag_combo.setCurrentText(items[0].tag or UNTAGGED)
            self.hidden_check.setChecked(all(e.hidden for e in items))
        edges = [e for e in items if isinstance(e, Edge)]
        self.soft_check.setVisible(bool(edges))
        self.soft_check.setChecked(bool(edges) and all(e.soft for e in edges))
        self._refreshing = False

    def _row_visible(self, widget: QWidget, visible: bool) -> None:
        widget.setVisible(visible)
        label = self.form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    @staticmethod
    def _area(mm2: float, units: str) -> str:
        """Area in model units, e.g. ``"1,500,000 mm² (1.5 m²)"``."""
        scale = from_mm(1.0, units)
        value = mm2 * scale * scale
        text = f"{value:,.0f} {units}²" if value >= 1000 else f"{value:.4g} {units}²"
        if units in ("mm", "cm") and mm2 >= 1e4:
            text += f" ({mm2 / 1e6:.4g} m²)"
        return text

    # -- edits (each one undoable) ----------------------------------------------------------
    def _name_changed(self) -> None:
        items = self.items()
        if self._refreshing or len(items) != 1 or not isinstance(items[0], ComponentInstance):
            return
        inst, name = items[0], self.name_edit.text().strip()
        if inst.name != name:
            self.perform("Rename", lambda: setattr(inst, "name", name))

    def _material_changed(self) -> None:
        if self._refreshing:
            return
        text = self.material_combo.currentText()
        material = None if text == DEFAULT else text
        targets = [e for e in self.items() if isinstance(e, (Face, ComponentInstance))]
        model = self.document.model
        self.perform("Paint", lambda: paint(model, targets, material, "front"))

    def _tag_changed(self) -> None:
        if self._refreshing:
            return
        text = self.tag_combo.currentText()
        tag = None if text == UNTAGGED else text
        items = self.items()

        def apply() -> None:
            for e in items:
                e.tag = tag

        self.perform("Set Tag", apply)

    def _hidden_changed(self) -> None:
        if self._refreshing:
            return
        items, hidden = self.items(), self.hidden_check.isChecked()

        def apply() -> None:
            for e in items:
                e.hidden = hidden

        self.perform("Hide" if hidden else "Unhide", apply)

    def _soft_changed(self) -> None:
        if self._refreshing:
            return
        edges = [e for e in self.items() if isinstance(e, Edge)]
        soft = self.soft_check.isChecked()

        def apply() -> None:
            for e in edges:
                e.soft = e.smooth = soft

        self.perform("Soften" if soft else "Unsoften", apply)
