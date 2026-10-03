"""Tags panel: create and delete tags, toggle their visibility."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from pymodeler.core.tags import UNTAGGED
from pymodeler.ui.panels.base import Panel


class TagsPanel(Panel):
    title = "Tags"

    def __init__(self, document, viewport, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(document, viewport, parent)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._toggled)
        add, delete = QPushButton("Add..."), QPushButton("Delete")
        add.clicked.connect(self.add_tag)
        delete.clicked.connect(self.delete_tag)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(delete)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        self._refreshing = True
        self.list.clear()
        for name, tag in self.document.model.tags.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if tag.visible else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self._refreshing = False

    def _toggled(self, item: QListWidgetItem) -> None:
        if self._refreshing:
            return
        self.set_visible(item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked)

    def set_visible(self, name: str, visible: bool) -> None:
        model = self.document.model
        if name not in model.tags or model.tags[name].visible == visible:
            return
        self.perform("Tag Visibility", lambda: setattr(model.tags[name], "visible", visible))

    def add_tag(self) -> None:
        name = self.ask_text("New tag", "Name:")
        if name and name not in self.document.model.tags:
            model = self.document.model
            self.perform("Add Tag", lambda: model.add_tag(name))

    def delete_tag(self) -> None:
        item = self.list.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not name or name == UNTAGGED:
            return
        model = self.document.model

        def delete() -> None:
            collections = [model.entities] + [d.entities for d in model.definitions.values()]
            for ents in collections:
                for e in ents.iter_entities():
                    if e.tag == name:
                        e.tag = None
            del model.tags[name]

        self.perform("Delete Tag", delete)
