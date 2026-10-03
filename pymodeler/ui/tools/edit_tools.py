"""Editing tools: Select (Space), Eraser (E), Push/Pull (P), Move (M), Rotate (Q),
Scale (S) and Offset (F).

They follow SketchUp's interaction: click-move-click (or drag), exact values in the
Measurements box, Ctrl to copy (Move/Rotate) or keep the original face (Push/Pull).
Every change is one undoable command using the same ``pymodeler.ops`` functions the
build scripts use.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPen

from pymodeler.core.components import ComponentInstance, entities_bounds
from pymodeler.core.entities import Edge, Entity, Face
from pymodeler.core.transform import rotation, scaling, translation
from pymodeler.core.units import format_length
from pymodeler.core.vec import GeometryError, Plane, normalize
from pymodeler.ops.extrude import _offset_polygon, offset_face, push_pull
from pymodeler.ops.organize import set_hidden
from pymodeler.ops.transform import copy_entities, transform_entities
from pymodeler.ui import overlay
from pymodeler.ui.inference import AXES, Inference
from pymodeler.ui.navigation import world_per_pixel
from pymodeler.ui.picking import closest_point_on_line_to_ray
from pymodeler.ui.selection import connected, expand_double, pick_entity, pick_face, rectangle_select
from pymodeler.ui.tools.base import Tool
from pymodeler.ui.tools.draw_tools import DrawingTool
from pymodeler.ui.vcb import VcbError, parse_angle_text, parse_lengths

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter

DRAG_PIXELS = 4.0


def _ctrl() -> bool:
    return bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)


def _shift() -> bool:
    return bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)


class _EditTool(DrawingTool):
    """Shared helpers for tools that change existing geometry."""

    @property
    def active(self):  # type: ignore[no-untyped-def]
        return self.document.active_entities

    def pick_scene(self):  # type: ignore[no-untyped-def]
        return self.viewport.inference_engine().scene

    def size(self) -> tuple[int, int]:
        return self.viewport.width(), self.viewport.height()

    def entity_under(self, x: float, y: float, prefer_faces: bool = False) -> Entity | None:
        w, h = self.size()
        return pick_entity(self.pick_scene(), self.viewport.camera, x, y, w, h, self.active, prefer_faces)

    def targets(self) -> list[Entity]:
        """The selection, or the entity under the cursor if nothing is selected."""
        items = [e for e in self.viewport.selection.items() if e.parent is self.active]
        if items:
            return items
        entity = self.entity_under(self.cursor.x(), self.cursor.y())
        if entity is None:
            return []
        items = expand_double(entity) if isinstance(entity, Face) else [entity]
        self.viewport.selection.set(items)
        return items

    def deactivate(self) -> None:
        super().deactivate()
        self.viewport.set_hover([])

    def reset(self) -> None:
        super().reset()
        if hasattr(self, "viewport"):
            self.viewport.set_hover([])


# ============================================================================ Select


class SelectTool(Tool):
    name = "Select"
    shortcut = "Space"
    hint = ("Click to select (Shift toggles, Ctrl adds). Drag right for window, left for crossing "
            "selection. Double-click: face + edges; triple-click: all connected.")

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.press: QPointF | None = None
        self.cursor = QPointF()
        self.dragging = False
        self._last_double = 0.0

    def reset(self) -> None:
        self.press = None
        self.dragging = False

    def _apply(self, items: list[Entity], modifiers: Qt.KeyboardModifier) -> None:
        sel = self.viewport.selection
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and shift:
            sel.remove(items)
        elif ctrl:
            sel.add(items)
        elif shift:
            sel.toggle(items)
        else:
            sel.set(items)

    def _pick(self, pos: QPointF) -> Entity | None:
        vp = self.viewport
        return pick_entity(vp.inference_engine().scene, vp.camera, pos.x(), pos.y(), vp.width(), vp.height(),
                           vp.document.active_entities)

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.press = event.position()
        self.cursor = event.position()
        self.dragging = False
        if time.monotonic() - self._last_double < QGuiApplication.styleHints().mouseDoubleClickInterval() / 1000:
            entity = self._pick(event.position())
            if entity is not None:
                self._apply(connected(entity), event.modifiers())
            self.press = None
        return True

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        if self.press is not None:
            d = self.cursor - self.press
            if abs(d.x()) + abs(d.y()) > DRAG_PIXELS:
                self.dragging = True
            return True
        return False

    def mouse_release(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton or self.press is None:
            return False
        vp = self.viewport
        if self.dragging:
            rect = (self.press.x(), self.press.y(), event.position().x(), event.position().y())
            crossing = event.position().x() < self.press.x()
            items = rectangle_select(vp.inference_engine().scene, vp.camera, rect, vp.width(), vp.height(),
                                     vp.document.active_entities, crossing)
            self._apply(items, event.modifiers())
        else:
            entity = self._pick(event.position())
            if entity is None:
                if not event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                    vp.selection.clear()
            else:
                self._apply([entity], event.modifiers())
        self.press = None
        self.dragging = False
        return True

    def mouse_double_click(self, event: "QMouseEvent") -> bool:
        entity = self._pick(event.position())
        self._last_double = time.monotonic()
        if entity is not None:
            self._apply(expand_double(entity), event.modifiers())
        return True

    def draw_overlay(self, painter: "QPainter") -> None:
        if self.press is None or not self.dragging:
            return
        crossing = self.cursor.x() < self.press.x()
        pen = QPen(QColor(30, 30, 30), 1.0)
        if crossing:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(80, 120, 220, 30))
        painter.drawRect(QRectF(self.press, self.cursor).normalized())

    def status(self) -> str:
        return f"{self.viewport.selection.describe()}. {self.hint}"


# ============================================================================ Eraser


class EraserTool(_EditTool):
    name = "Eraser"
    shortcut = "E"
    hint = "Click or drag over edges and groups to erase them. Shift hides instead, Ctrl softens."

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.marked: list[Entity] = []
        self.erasing = False

    def reset(self) -> None:
        super().reset()
        self.marked = []
        self.erasing = False

    def _mark(self, x: float, y: float) -> None:
        entity = self.entity_under(x, y)
        if isinstance(entity, Face):
            entity = None  # SketchUp's eraser works on edges (and groups), not faces
        if entity is not None and entity not in self.marked:
            self.marked.append(entity)
            self.viewport.set_hover(list(self.marked))

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.erasing = True
        self.marked = []
        self._mark(event.position().x(), event.position().y())
        return True

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        if self.erasing:
            self._mark(self.cursor.x(), self.cursor.y())
        else:
            entity = self.entity_under(self.cursor.x(), self.cursor.y())
            self.viewport.set_hover([] if entity is None or isinstance(entity, Face) else [entity])
        return True

    def mouse_release(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self.erasing:
            return False
        items = [e for e in self.marked if e.alive]
        self.erasing = False
        self.marked = []
        self.viewport.set_hover([])
        if not items:
            return True
        active = self.active
        if _shift():
            self.commit("Hide", lambda: set_hidden(items, True))
        elif _ctrl():
            self.commit("Soften", lambda: _soften(items))
        else:
            self.commit("Erase", lambda: active.erase(items))
        return True

    def draw_overlay(self, painter: "QPainter") -> None:
        painter.setPen(QPen(QColor(60, 60, 60), 1.0))
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(self.cursor, 6, 6)


def _soften(items: list[Entity]) -> None:
    for e in items:
        if isinstance(e, Edge):
            e.soft = e.smooth = True


# ============================================================================ Push/Pull


class PushPullTool(_EditTool):
    name = "Push/Pull"
    shortcut = "P"
    hint = "Click a face, move to push or pull it, click again. Type a distance. Ctrl keeps the original face."
    vcb_label = "Distance"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.face: Face | None = None
        self.origin = np.zeros(3)
        self.normal = np.array([0.0, 0.0, 1.0])
        self.distance = 0.0
        self.last_distance: float | None = None
        self.press_pos: QPointF | None = None

    def reset(self) -> None:
        super().reset()
        self.face = None
        self.distance = 0.0
        self.press_pos = None

    def _hover_face(self, x: float, y: float) -> Face | None:
        w, h = self.size()
        hit = pick_face(self.pick_scene(), self.viewport.camera, x, y, w, h, self.active)
        return hit.entity if hit is not None else None  # type: ignore[return-value]

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        if self.face is None:
            face = self._hover_face(self.cursor.x(), self.cursor.y())
            self.viewport.set_hover([face] if face is not None else [])
            return True
        self.distance = self._distance_at(self.cursor.x(), self.cursor.y())
        return True

    def _distance_at(self, x: float, y: float) -> float:
        inference = self.infer(x, y)
        if inference.kind in ("endpoint", "midpoint", "on_edge", "origin"):
            return float((inference.point - self.origin) @ self.normal)
        origin, direction = self.viewport.ray(x, y)
        point = closest_point_on_line_to_ray(self.origin, self.normal, origin, direction)
        return float((point - self.origin) @ self.normal)

    def infer(self, x: float, y: float, plane: Plane | None = None) -> Inference:
        engine = self.viewport.inference_engine()
        return engine.infer(self.viewport.camera, x, y, *self.size())

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.cursor = event.position()
        if self.face is None:
            face = self._hover_face(self.cursor.x(), self.cursor.y())
            if face is None:
                self.message = "Click on a face"
                return True
            origin, direction = self.viewport.ray(self.cursor.x(), self.cursor.y())
            plane = face.plane
            t = -plane.distance(origin) / float(plane.normal @ direction)
            self.face = face
            self.origin = origin + direction * t
            self.normal = face.normal.copy()
            self.distance = 0.0
            self.press_pos = event.position()
            self.message = ""
            return True
        self.finish(self.distance)
        return True

    def mouse_release(self, event: "QMouseEvent") -> bool:
        if self.face is not None and self.press_pos is not None:
            d = event.position() - self.press_pos
            if abs(d.x()) + abs(d.y()) > DRAG_PIXELS:  # press-drag-release
                self.finish(self._distance_at(event.position().x(), event.position().y()))
            self.press_pos = None
            return True
        return False

    def mouse_double_click(self, event: "QMouseEvent") -> bool:
        face = self._hover_face(event.position().x(), event.position().y())
        if face is not None and self.last_distance is not None:
            self.reset()
            active, d = self.active, self.last_distance
            self.commit("Push/Pull", lambda: push_pull(active, face, d))
        return True

    def finish(self, distance: float) -> None:
        if self.face is None:
            return
        if abs(distance) < 1e-6:
            self.reset()
            return
        face, active, keep = self.face, self.active, _ctrl()
        self.last_distance = distance
        self.reset()
        self.commit("Push/Pull", lambda: push_pull(active, face, distance, create_new=keep))

    def vcb_entered(self, text: str) -> bool:
        try:
            (distance,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if self.face is None:
            self.message = "Click a face first"
            return False
        sign = -1.0 if self.distance < 0 else 1.0
        self.finish(distance * sign)
        return True

    def preview(self, painter: "QPainter") -> None:
        if self.face is None or not self.face.alive:
            return
        offset = self.normal * self.distance
        for loop in self.face.loops:
            pts = [v.position + offset for v in loop]
            overlay.polyline(painter, self.viewport, pts, overlay.PREVIEW, 2.0, closed=True)
        for v in self.face.outer_loop:
            overlay.polyline(painter, self.viewport, [v.position, v.position + offset], overlay.PREVIEW, 1.0)

    def draw_overlay(self, painter: "QPainter") -> None:
        self.preview(painter)

    def hint_for_state(self) -> str:
        if self.face is None:
            return self.hint
        return f"Distance {self.fmt(self.distance)}. Click to finish or type a distance."


# ============================================================================ Move / Rotate / Scale


class _TransformTool(_EditTool):
    """Shared behaviour for Move, Rotate and Scale (targets, copy mode, preview)."""

    copy_allowed = True

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.items: list[Entity] = []
        self.copy = False

    def reset(self) -> None:
        super().reset()
        self.items = []
        self.copy = False

    def key_press(self, event: "QKeyEvent") -> bool:
        if event.key() == Qt.Key.Key_Control and self.copy_allowed:
            self.copy = not self.copy
            return True
        return super().key_press(event)

    def matrix(self) -> np.ndarray | None:
        """The transform the tool would apply now."""
        return None

    def apply(self, matrix: np.ndarray, name: str) -> list[Entity]:
        """Move or copy the targets; returns what ends up selected."""
        items, active, copy = list(self.items), self.active, self.copy
        result: list[Entity] = []

        def action() -> None:
            if copy:
                result.extend(copy_entities(active, items, matrix).entities)
            else:
                transform_entities(active, items, matrix)
                result.extend(items)

        self.commit(name + (" Copy" if copy else ""), action)
        return result

    def preview_outline(self, painter: "QPainter", matrix: np.ndarray) -> None:
        for e in self.items[:400]:
            if isinstance(e, Edge):
                pts = [matrix[:3, :3] @ v.position + matrix[:3, 3] for v in e.vertices]
                overlay.polyline(painter, self.viewport, pts, QColor(30, 80, 220), 1.5)
            elif isinstance(e, Face):
                pts = [matrix[:3, :3] @ v.position + matrix[:3, 3] for v in e.outer_loop]
                overlay.polyline(painter, self.viewport, pts, QColor(30, 80, 220), 1.5, closed=True)
            elif isinstance(e, ComponentInstance):
                box = entities_bounds(e.definition.entities, matrix @ e.transform)
                if box is not None:
                    lo, hi = box
                    ring = [np.array([lo[0], lo[1], lo[2]]), np.array([hi[0], lo[1], lo[2]]),
                            np.array([hi[0], hi[1], lo[2]]), np.array([lo[0], hi[1], lo[2]])]
                    overlay.polyline(painter, self.viewport, ring, QColor(30, 80, 220), 1.5, closed=True)
                    overlay.polyline(painter, self.viewport, [r + [0, 0, hi[2] - lo[2]] for r in ring],
                                     QColor(30, 80, 220), 1.5, closed=True)


class MoveTool(_TransformTool):
    name = "Move"
    shortcut = "M"
    hint = "Click a point to move from (the selection, or what is under the cursor). Ctrl copies."
    vcb_label = "Distance"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.last_copy: tuple[list[Entity], np.ndarray, list[Entity]] | None = None

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.items = self.targets()
            if not self.items:
                self.message = "Nothing to move: select something or click on it"
                return
            self.points = [inference.point.copy()]
            self.message = ""
            return
        self.finish(inference.point - self.points[0])

    def finish(self, delta: np.ndarray) -> None:
        if np.linalg.norm(delta) < 1e-9:
            self.reset()
            return
        copy, items = self.copy, list(self.items)
        result = self.apply(translation(delta), "Move")
        self.last_copy = (items, delta, result) if copy else None
        self.viewport.selection.set(result)
        self.reset()

    def matrix(self) -> np.ndarray | None:
        if not self.points or self.current is None:
            return None
        return translation(self.current.point - self.points[0])

    def vcb_entered(self, text: str) -> bool:
        stripped = text.strip().lower()
        if self.last_copy is not None and not self.points and (stripped.endswith("x") or stripped.startswith("/")):
            return self._multiply(stripped)
        try:
            (distance,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if not self.points or self.current is None:
            self.message = "Pick the point to move from, then show the direction"
            return False
        d = self.current.point - self.points[0]
        if np.linalg.norm(d) < 1e-9:
            self.message = "Move the mouse to show the direction first"
            return False
        self.finish(normalize(d) * distance)
        return True

    def _multiply(self, text: str) -> bool:
        """After a copy: ``5x`` makes 5 copies in a row, ``/5`` divides the gap into 5."""
        assert self.last_copy is not None
        items, delta, copies = self.last_copy
        try:
            count = int(text[1:] if text.startswith("/") else text[:-1].strip("*"))
        except ValueError:
            self.message = "type e.g. 5x or /5"
            return False
        if count < 1 or count > 1000:
            self.message = "count must be between 1 and 1000"
            return False
        step = delta / count if text.startswith("/") else delta
        active = self.active

        def action() -> None:
            active.erase([c for c in copies if c.alive])
            for k in range(1, count + 1):
                copy_entities(active, [i for i in items if i.alive], translation(step * k))

        self.commit("Array", action)
        self.last_copy = None
        return True

    def preview(self, painter: "QPainter") -> None:
        matrix = self.matrix()
        if matrix is not None:
            overlay.polyline(painter, self.viewport, [self.points[0], self.current.point], overlay.RUBBER_BAND, 1.0, dashed=True)  # type: ignore[union-attr]
            self.preview_outline(painter, matrix)

    def hint_for_state(self) -> str:
        if not self.points:
            return self.hint + (" Type 5x or /5 to repeat the last copy." if self.last_copy else "")
        d = 0.0 if self.current is None else float(np.linalg.norm(self.current.point - self.points[0]))
        mode = " (copy)" if self.copy else ""
        return f"Move{mode} {self.fmt(d)}. Click the destination or type a distance. Arrows lock an axis."


class RotateTool(_TransformTool):
    name = "Rotate"
    shortcut = "Q"
    hint = "Click the centre of rotation (on a face to rotate in its plane). Ctrl copies."
    vcb_label = "Angle"
    SNAP = 15.0

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.axis = np.array([0.0, 0.0, 1.0])
        self.angle = 0.0

    def reset(self) -> None:
        super().reset()
        self.angle = 0.0

    def toggle_lock(self, axis: str) -> None:
        if not self.points:
            self.axis = AXES[axis].copy()
            self.lock_name = f"rotate about {axis.upper()}"
        else:
            super().toggle_lock(axis)

    def drawing_plane(self) -> Plane | None:
        return Plane.from_point_normal(self.points[0], self.axis) if self.points else None

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.items = self.targets()
            if not self.items:
                self.message = "Nothing to rotate: select something or click on it"
                return
            if self.lock_name is None and inference.kind == "on_face" and inference.plane is not None:
                self.axis = inference.plane.normal.copy()
            elif self.lock_name is None:
                self.axis = np.array([0.0, 0.0, 1.0])
            self.points = [inference.point.copy()]
            self.message = ""
            return
        if len(self.points) == 1:
            ref = self._in_plane(inference.point)
            if np.linalg.norm(ref - self.points[0]) > 1e-6:
                self.points.append(ref)
            return
        self.finish(self.angle)

    def _in_plane(self, p: np.ndarray) -> np.ndarray:
        return Plane.from_point_normal(self.points[0], self.axis).project(p)

    def mouse_move(self, event: "QMouseEvent") -> bool:
        super().mouse_move(event)
        if len(self.points) == 2 and self.current is not None:
            self.angle = self._angle_to(self._cursor_on_plane())
        return True

    def _cursor_on_plane(self) -> np.ndarray:
        plane = Plane.from_point_normal(self.points[0], self.axis)
        origin, direction = self.viewport.ray(self.cursor.x(), self.cursor.y())
        denom = float(plane.normal @ direction)
        if abs(denom) < 1e-9:
            return self._in_plane(self.current.point)  # type: ignore[union-attr]
        return origin + direction * (-plane.distance(origin) / denom)

    def _angle_to(self, p: np.ndarray) -> float:
        c, ref = self.points[0], self.points[1]
        u = normalize(ref - c)
        v = np.cross(self.axis, u)
        d = p - c
        if np.linalg.norm(d) < 1e-9:
            return 0.0
        angle = math.degrees(math.atan2(float(d @ v), float(d @ u)))
        snapped = round(angle / self.SNAP) * self.SNAP
        return snapped if abs(snapped - angle) < 2.5 else angle

    def finish(self, angle: float) -> None:
        if abs(angle) < 1e-9:
            self.reset()
            return
        result = self.apply(rotation(self.axis, angle, self.points[0]), "Rotate")
        self.viewport.selection.set(result)
        self.reset()

    def matrix(self) -> np.ndarray | None:
        if len(self.points) < 2:
            return None
        return rotation(self.axis, self.angle, self.points[0])

    def vcb_entered(self, text: str) -> bool:
        try:
            angle = parse_angle_text(text)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if len(self.points) < 2:
            self.message = "Click the centre and a reference point first"
            return False
        self.finish(math.copysign(angle, self.angle) if self.angle else angle)
        return True

    def preview(self, painter: "QPainter") -> None:
        if not self.points:
            return
        c = self.points[0]
        radius = world_per_pixel(self.viewport.camera, self.viewport.height()) * 60
        if self.viewport.camera.perspective:
            radius *= float(np.linalg.norm(c - self.viewport.camera.eye)) / max(self.viewport.camera.distance(), 1e-9)
        u = normalize(self.points[1] - c) if len(self.points) > 1 else _perp(self.axis)
        v = np.cross(self.axis, u)
        ring = [c + radius * (math.cos(2 * math.pi * k / 48) * u + math.sin(2 * math.pi * k / 48) * v) for k in range(48)]
        overlay.polyline(painter, self.viewport, ring, QColor(40, 70, 230), 1.5, closed=True)
        if len(self.points) > 1:
            overlay.polyline(painter, self.viewport, [c, self.points[1]], overlay.RUBBER_BAND, 1.0, dashed=True)
            a = math.radians(self.angle)
            end = c + float(np.linalg.norm(self.points[1] - c)) * (math.cos(a) * u + math.sin(a) * v)
            overlay.polyline(painter, self.viewport, [c, end], overlay.RUBBER_BAND, 1.5)
            matrix = self.matrix()
            if matrix is not None:
                self.preview_outline(painter, matrix)

    def hint_for_state(self) -> str:
        if not self.points:
            return self.hint + " Arrow keys pick the axis."
        if len(self.points) == 1:
            return "Click a reference point to start the rotation."
        return f"Angle {self.angle:.1f}°. Click to finish or type an angle (snaps every 15°)."


def _perp(n: np.ndarray) -> np.ndarray:
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    return normalize(np.cross(n, np.cross(helper, n)))


class ScaleTool(_TransformTool):
    name = "Scale"
    shortcut = "S"
    hint = "Click the fixed point, then a handle point; move to scale. Type a factor (2 or 1.5,1,1)."
    vcb_label = "Scale"
    copy_allowed = False

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.factor = 1.0

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.items = self.targets()
            if not self.items:
                self.message = "Nothing to scale: select something or click on it"
                return
            self.points = [inference.point.copy()]
            self.message = ""
            return
        if len(self.points) == 1:
            if np.linalg.norm(inference.point - self.points[0]) > 1e-6:
                self.points.append(inference.point.copy())
                self.factor = 1.0
            return
        self.finish(self.factor)

    def mouse_move(self, event: "QMouseEvent") -> bool:
        super().mouse_move(event)
        if len(self.points) == 2 and self.current is not None:
            base, handle = self.points
            d = handle - base
            length = float(np.linalg.norm(d))
            origin, direction = self.viewport.ray(self.cursor.x(), self.cursor.y())
            p = closest_point_on_line_to_ray(base, d / length, origin, direction)
            self.factor = max(1e-3, float((p - base) @ (d / length)) / length)
        return True

    def finish(self, factor: float | np.ndarray) -> None:
        if np.allclose(factor, 1.0):
            self.reset()
            return
        try:
            matrix = scaling(factor, self.points[0])
        except GeometryError as exc:
            self.message = str(exc)
            return
        result = self.apply(matrix, "Scale")
        self.viewport.selection.set(result)
        self.reset()

    def matrix(self) -> np.ndarray | None:
        if len(self.points) < 2:
            return None
        return scaling(self.factor, self.points[0])

    def vcb_entered(self, text: str) -> bool:
        parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
        try:
            factors = [float(p) for p in parts]
        except ValueError:
            self.message = "type a factor such as 2, 0.5 or 1.5,1,1"
            return False
        if len(factors) not in (1, 3) or not self.points:
            self.message = "Click the fixed point first, then type 1 or 3 factors"
            return False
        self.finish(factors[0] if len(factors) == 1 else np.array(factors))
        return True

    def preview(self, painter: "QPainter") -> None:
        matrix = self.matrix()
        if matrix is not None:
            overlay.polyline(painter, self.viewport, self.points, overlay.RUBBER_BAND, 1.0, dashed=True)
            self.preview_outline(painter, matrix)

    def hint_for_state(self) -> str:
        if not self.points:
            return self.hint
        if len(self.points) == 1:
            return "Click a handle point (scaling happens along the line from the fixed point)."
        return f"Scale {self.factor:.3f}. Click to finish or type a factor."


# ============================================================================ Offset


class OffsetTool(_EditTool):
    name = "Offset"
    shortcut = "F"
    hint = "Click a face, then move inward or outward and click. Type a distance (positive = inward)."
    vcb_label = "Distance"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.face: Face | None = None
        self.distance = 0.0

    def reset(self) -> None:
        super().reset()
        self.face = None
        self.distance = 0.0

    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        w, h = self.size()
        if self.face is None:
            hit = pick_face(self.pick_scene(), self.viewport.camera, self.cursor.x(), self.cursor.y(), w, h, self.active)
            self.viewport.set_hover([hit.entity] if hit is not None else [])
            return True
        self.distance = self._distance_at(self.cursor.x(), self.cursor.y())
        return True

    def _distance_at(self, x: float, y: float) -> float:
        assert self.face is not None
        plane = self.face.plane
        origin, direction = self.viewport.ray(x, y)
        denom = float(plane.normal @ direction)
        if abs(denom) < 1e-9:
            return self.distance
        p = origin + direction * (-plane.distance(origin) / denom)
        q = plane.to_2d([p])[0]
        loops = self.face.loops_2d(plane)
        from pymodeler.core.planar import distance_to_polygon_boundary, point_in_loops

        d = min(distance_to_polygon_boundary(q, loop) for loop in loops)
        return d if point_in_loops(q, loops) else -d

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.cursor = event.position()
        w, h = self.size()
        if self.face is None:
            hit = pick_face(self.pick_scene(), self.viewport.camera, self.cursor.x(), self.cursor.y(), w, h, self.active)
            if hit is None:
                self.message = "Click on a face"
                return True
            self.face = hit.entity  # type: ignore[assignment]
            self.message = ""
            return True
        self.finish(self.distance)
        return True

    def finish(self, distance: float) -> None:
        if self.face is None or abs(distance) < 1e-6:
            return
        face, active = self.face, self.active
        if self.commit("Offset", lambda: offset_face(active, face, distance)):
            self.reset()

    def vcb_entered(self, text: str) -> bool:
        try:
            (distance,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if self.face is None:
            self.message = "Click a face first"
            return False
        self.finish(distance)
        return True

    def preview(self, painter: "QPainter") -> None:
        if self.face is None or not self.face.alive or abs(self.distance) < 1e-6:
            return
        plane = self.face.plane
        try:
            loop = _offset_polygon(self.face.loops_2d(plane)[0], self.distance)
        except GeometryError:
            return
        overlay.polyline(painter, self.viewport, [plane.to_3d(p) for p in loop], overlay.PREVIEW, 2.0, closed=True)

    def draw_overlay(self, painter: "QPainter") -> None:
        self.preview(painter)

    def hint_for_state(self) -> str:
        if self.face is None:
            return self.hint
        return f"Offset {format_length(self.distance, self.units)}. Click to finish or type a distance."


EDIT_TOOLS: dict[str, Callable] = {
    "select": SelectTool,
    "eraser": EraserTool,
    "push_pull": PushPullTool,
    "move": MoveTool,
    "rotate": RotateTool,
    "scale": ScaleTool,
    "offset": OffsetTool,
}
