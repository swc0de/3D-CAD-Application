"""The Paint Bucket tool (B)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Face
from pymodeler.core.transform import apply_normal
from pymodeler.ops.organize import paint
from pymodeler.ui.tools.edit_tools import _EditTool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent, QPainter


class PaintTool(_EditTool):
    name = "Paint Bucket"
    shortcut = "B"
    hint = "Click a face or group to paint it with the current material. Alt+click picks up a material."

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        entity = self.entity_under(self.cursor.x(), self.cursor.y(), prefer_faces=True)
        self.viewport.set_hover([entity] if isinstance(entity, (Face, ComponentInstance)) else [])
        return True

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.cursor = event.position()
        entity = self.entity_under(self.cursor.x(), self.cursor.y(), prefer_faces=True)
        if entity is None:
            return True
        back = isinstance(entity, Face) and self._sees_back(entity)
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            sampled = (entity.back_material if back else entity.material) if isinstance(entity, Face) else (
                entity.material if isinstance(entity, ComponentInstance) else None)
            self.viewport.current_material = sampled
            if self.viewport.on_material_sampled is not None:
                self.viewport.on_material_sampled(sampled)
            return True
        material = self.viewport.current_material
        side = "back" if back else "front"
        model = self.document.model
        targets = list(self.viewport.selection.items()) if entity in self.viewport.selection else [entity]
        self.commit("Paint", lambda: paint(model, targets, material, side))
        return True

    def _sees_back(self, face: Face) -> bool:
        """Whether the camera is looking at the face's back side."""
        normal = apply_normal(self.document.context_transform, face.normal)
        point = self.document.to_world(face.outer_loop[0].position)
        cam = self.viewport.camera
        view = point - cam.eye if cam.perspective else -cam.basis()[2]
        return float(np.dot(normal, view)) > 0

    def draw_overlay(self, painter: "QPainter") -> None:
        name = self.viewport.current_material or "Default"
        material = self.document.model.materials.get(name)
        color = QColor(*(int(c * 255) for c in material.color)) if material else QColor(240, 240, 235)
        painter.setPen(QPen(QColor(40, 40, 40), 1.0))
        painter.setBrush(color)
        painter.drawRect(int(self.cursor.x()) + 10, int(self.cursor.y()) + 10, 14, 14)

    def status(self) -> str:
        return f"{self.hint} Current: {self.viewport.current_material or 'Default'}."
