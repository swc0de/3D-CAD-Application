"""Materials panel: pick the Paint Bucket's material; create, edit and delete materials."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from pymodeler.core.materials import DEFAULT_FRONT_COLOR, Material
from pymodeler.ui.panels.base import Panel

DEFAULT_LABEL = "Default"


def swatch(color: tuple[float, float, float], opacity: float = 1.0, size: int = 22) -> QIcon:
    """A square colour icon (checkered when transparent)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(*(int(c * 255) for c in color), int(opacity * 255) if opacity < 1 else 255))
    return QIcon(pixmap)


class MaterialsPanel(Panel):
    title = "Materials"

    def __init__(self, document, viewport, on_pick=None, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(document, viewport, parent)
        self.on_pick = on_pick
        self.list = QListWidget()
        self.list.setIconSize(QSize(22, 22))
        self.list.itemClicked.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda _item: self.edit_material())
        new, edit, delete = QPushButton("New..."), QPushButton("Edit..."), QPushButton("Delete")
        new.clicked.connect(self.new_material)
        edit.clicked.connect(self.edit_material)
        delete.clicked.connect(self.delete_material)
        buttons = QHBoxLayout()
        for b in (new, edit, delete):
            buttons.addWidget(b)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        viewport.on_material_sampled = lambda name: self.refresh()
        self.refresh()

    def refresh(self) -> None:
        current = self.viewport.current_material
        self.list.clear()
        items = [(DEFAULT_LABEL, None, swatch(DEFAULT_FRONT_COLOR))]
        for name, m in sorted(self.document.model.materials.items()):
            items.append((f"{name}{'  (' + format(m.opacity, '.0%') + ')' if m.is_transparent else ''}", name,
                          swatch(m.color, m.opacity)))
        for label, name, icon in items:
            item = QListWidgetItem(icon, label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
            if name == current:
                self.list.setCurrentItem(item)

    def selected_name(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _picked(self, item: QListWidgetItem) -> None:
        self.viewport.current_material = item.data(Qt.ItemDataRole.UserRole)
        if self.on_pick is not None:
            self.on_pick()

    def pick(self, name: str | None) -> None:
        """Select a material programmatically (as if clicked)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentItem(item)
                self._picked(item)
                return

    def new_material(self) -> None:
        name = self.ask_text("New material", "Name:")
        if not name:
            return
        if name in self.document.model.materials:
            self.window().statusBar().showMessage(f"A material called {name!r} already exists", 5000)
            return
        color = self.ask_color(QColor(200, 180, 150))
        if color is None:
            return
        model = self.document.model
        material = Material(name, (color.redF(), color.greenF(), color.blueF()))
        self.perform("New Material", lambda: model.add_material(material))
        self.viewport.current_material = name
        self.refresh()

    def edit_material(self) -> None:
        name = self.selected_name()
        material = self.document.model.materials.get(name) if name else None
        if material is None:
            return
        color = self.ask_color(QColor(*(int(c * 255) for c in material.color)))
        if color is None:
            return
        opacity = self.ask_number("Opacity", "Opacity (0 = clear, 1 = solid):", material.opacity, 0.0, 1.0)
        if opacity is None:
            return
        model = self.document.model

        def change() -> None:
            m = model.materials[name]  # type: ignore[index]
            m.color = (color.redF(), color.greenF(), color.blueF())
            m.opacity = float(opacity)

        self.perform("Edit Material", change)

    def delete_material(self) -> None:
        name = self.selected_name()
        if not name:
            return
        model = self.document.model

        def delete() -> None:
            collections = [model.entities] + [d.entities for d in model.definitions.values()]
            for ents in collections:
                for f in ents.faces.values():
                    if f.material == name:
                        f.material = None
                    if f.back_material == name:
                        f.back_material = None
                for inst in ents.instances.values():
                    if inst.material == name:
                        inst.material = None
            del model.materials[name]

        self.perform("Delete Material", delete)
        if self.viewport.current_material == name:
            self.viewport.current_material = None
