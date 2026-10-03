"""Outliner: the tree of groups and components."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Entities
from pymodeler.ui.panels.base import Panel


class OutlinerPanel(Panel):
    title = "Outliner"

    def __init__(self, document, viewport, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(document, viewport, parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tree)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        root = QTreeWidgetItem([self.document.model.name or "Model", "Model"])
        self.tree.addTopLevelItem(root)
        self._add_children(root, self.document.model.entities, ())
        root.setExpanded(True)
        for i in range(root.childCount()):
            root.child(i).setExpanded(True)

    def _add_children(self, parent: QTreeWidgetItem, ents: Entities, path: tuple) -> None:
        if len(path) > 16:
            return
        for inst in ents.instances.values():
            kind = "Group" if inst.is_group else f"Component ({inst.definition.name})"
            label = inst.name or inst.definition.name
            item = QTreeWidgetItem([label, kind])
            item.setData(0, Qt.ItemDataRole.UserRole, path + (inst,))
            if inst.hidden:
                item.setForeground(0, Qt.GlobalColor.gray)
            if inst in self.document.edit_path:  # open for editing
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            parent.addChild(item)
            self._add_children(item, inst.definition.entities, path + (inst,))

    def item_for(self, instance: ComponentInstance) -> QTreeWidgetItem | None:
        """The tree item showing an instance (for tests and selection sync)."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and path[-1] is instance:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    def selection_changed(self) -> None:
        """Highlight the selected group/component in the tree."""
        items = self.viewport.selection.items()
        item = self.item_for(items[0]) if len(items) == 1 and isinstance(items[0], ComponentInstance) else None
        self.tree.blockSignals(True)
        if item is not None:
            self.tree.setCurrentItem(item)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)

    # Item signals are handled after Qt has finished with the item, because acting on
    # them rebuilds the tree (deleting the item).
    def _clicked(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            QTimer.singleShot(0, lambda: self.select_path(path))

    def _double_clicked(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            QTimer.singleShot(0, lambda: self.open_path(path))

    def select_path(self, path: tuple[ComponentInstance, ...]) -> None:
        """Select an instance, opening its parent groups so it can be selected."""
        instance = self._open_parents(path)
        if instance is not None:
            self.viewport.selection.set([instance])

    def open_path(self, path: tuple[ComponentInstance, ...]) -> None:
        """Open an instance for editing (and its parents)."""
        instance = self._open_parents(path)
        if instance is not None:
            self.viewport.selection.clear()
            self.document.enter(instance)

    def _open_parents(self, path: tuple[ComponentInstance, ...]) -> ComponentInstance | None:
        """Make the instance's parent the active context; returns the instance.

        Opening a shared group makes it unique, which replaces its contents, so the path
        is followed by position rather than by the (possibly stale) instances.
        """
        indices: list[int] = []
        parent = self.document.model.entities
        for inst in path:
            members = list(parent.instances.values())
            if inst not in members:
                return None
            indices.append(members.index(inst))
            parent = inst.definition.entities
        doc = self.document
        if path[-1].parent is doc.active_entities:
            return path[-1]  # already in the right context
        while doc.edit_path:
            doc.exit()
        for index in indices[:-1]:
            doc.enter(list(doc.active_entities.instances.values())[index])
        return list(doc.active_entities.instances.values())[indices[-1]]
