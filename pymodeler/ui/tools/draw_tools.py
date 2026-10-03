"""Drawing tools: Line (L), Rectangle (R), Circle (C), Arc (A) and Polygon.

Each tool snaps the cursor with the inference engine, previews what it will draw, and
accepts exact values in the Measurements box. Geometry is created with the same
``pymodeler.ops.draw`` functions the build scripts use, as one undoable command.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import numpy as np
from PySide6.QtCore import QPointF, Qt

from pymodeler.core.units import format_length
from pymodeler.core.vec import GeometryError, Plane, normalize
from pymodeler.ops import draw
from pymodeler.ui import overlay
from pymodeler.ui.inference import AXES, Inference
from pymodeler.ui.tools.base import Tool
from pymodeler.ui.vcb import VcbError, parse_lengths, parse_segments

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter

GROUND = Plane.from_point_normal((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
ARROW_AXES = {Qt.Key.Key_Right: "x", Qt.Key.Key_Left: "y", Qt.Key.Key_Up: "z"}


class DrawingTool(Tool):
    """Shared behaviour: inference, axis locks, previews and committing commands."""

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.points: list[np.ndarray] = []
        self.current: Inference | None = None
        self.cursor = QPointF()
        self.lock: np.ndarray | None = None
        self.lock_name: str | None = None
        self.message = ""

    # -- helpers ------------------------------------------------------------------------
    @property
    def document(self):  # type: ignore[no-untyped-def]
        return self.viewport.document

    @property
    def units(self) -> str:
        return self.document.model.units

    def fmt(self, mm: float) -> str:
        return format_length(mm, self.units)

    def reset(self) -> None:
        self.points = []
        self.lock = None
        self.lock_name = None
        self.message = ""

    def deactivate(self) -> None:
        self.reset()
        self.current = None

    def infer(self, x: float, y: float, plane: Plane | None = None) -> Inference:
        start = self.points[-1] if self.points else None
        engine = self.viewport.inference_engine()
        return engine.infer(self.viewport.camera, x, y, self.viewport.width(), self.viewport.height(),
                            start=start, lock=self.lock, plane=plane)

    def local(self, point: np.ndarray) -> np.ndarray:
        """A world point in the coordinates of the collection being edited."""
        return self.document.to_local(point)

    def local_axes(self, axes: draw.PlaneAxes) -> draw.PlaneAxes:
        """World drawing axes expressed in the collection being edited."""
        u = normalize(self.document.to_local_vector(axes[0]))
        v = normalize(self.document.to_local_vector(axes[1]))
        return u, v, normalize(np.cross(u, v))

    def commit(self, name: str, action: Callable[[], object]) -> bool:
        """Run ``action`` as an undoable command; report errors in the status bar."""
        try:
            self.document.perform(name, action)
        except (GeometryError, ValueError) as exc:
            self.message = f"Could not draw: {exc}"
            return False
        self.message = ""
        return True

    # -- events ---------------------------------------------------------------------------
    def mouse_move(self, event: "QMouseEvent") -> bool:
        self.cursor = event.position()
        self.current = self.infer(self.cursor.x(), self.cursor.y(), self.drawing_plane())
        return True

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.cursor = event.position()
        self.current = self.infer(self.cursor.x(), self.cursor.y(), self.drawing_plane())
        self.click(self.current)
        return True

    def key_press(self, event: "QKeyEvent") -> bool:
        key = event.key()
        if key in ARROW_AXES:
            self.toggle_lock(ARROW_AXES[key])
            return True
        if key == Qt.Key.Key_Shift and self.current is not None and self.points:
            if self.lock is None and self.current.guide is not None:
                d = self.current.point - self.points[-1]
                if np.linalg.norm(d) > 1e-6:
                    self.lock = normalize(d)
                    self.lock_name = self.current.label or "Locked"
            elif self.lock is not None:
                self.lock = None
                self.lock_name = None
            return True
        return False

    def toggle_lock(self, axis: str) -> None:
        """Lock (or unlock) drawing to a red/green/blue direction."""
        if self.lock_name == axis:
            self.lock, self.lock_name = None, None
        else:
            self.lock, self.lock_name = AXES[axis].copy(), axis

    # -- to override ------------------------------------------------------------------------
    def drawing_plane(self) -> Plane | None:
        """Plane used when nothing is snapped (default: the ground / start height)."""
        return None

    def click(self, inference: Inference) -> None:
        """Handle a click at an inferred point."""

    def preview(self, painter: "QPainter") -> None:
        """Draw the shape in progress."""

    # -- overlay ----------------------------------------------------------------------------
    def draw_overlay(self, painter: "QPainter") -> None:
        self.preview(painter)
        if self.current is not None:
            overlay.marker(painter, self.viewport, self.current)
            overlay.label(painter, self.cursor, self.current.label)

    def status(self) -> str:
        text = self.hint_for_state()
        if self.lock_name:
            text += f"  [locked: {self.lock_name}]"
        return f"{self.message}  {text}" if self.message else text

    def hint_for_state(self) -> str:
        return self.hint


def _plane_from(inference: Inference | None, point: np.ndarray, lock: str | None = None) -> Plane:
    """The plane a shape is drawn on: a locked axis plane, the face under the
    cursor, or the horizontal plane through the point."""
    if lock in AXES:
        return Plane.from_point_normal(point, AXES[lock])
    if inference is not None and inference.kind == "on_face" and inference.plane is not None:
        return Plane.from_point_normal(point, inference.plane.normal)
    return Plane.from_point_normal(point, (0.0, 0.0, 1.0))


def _on_plane(tool: DrawingTool, plane: Plane) -> np.ndarray | None:
    """The cursor projected onto ``plane`` (snapped points are projected too)."""
    if tool.current is not None and tool.current.kind not in ("plane", "on_face"):
        return plane.project(tool.current.point)
    origin, direction = tool.viewport.ray(tool.cursor.x(), tool.cursor.y())
    denom = float(plane.normal @ direction)
    if abs(denom) < 1e-9:
        return None
    t = -plane.distance(origin) / denom
    return origin + direction * t if t > 0 else None


def _axes_for(plane: Plane) -> draw.PlaneAxes:
    """Drawing axes for a plane, matching the named planes for axis-aligned normals."""
    n = plane.normal
    if float(n[2]) < -0.999:
        n = -n
    return draw.plane_axes(normal=n)


# ============================================================================ Line


class LineTool(DrawingTool):
    name = "Line"
    shortcut = "L"
    hint = "Click to start a line."
    vcb_label = "Length"

    def click(self, inference: Inference) -> None:
        self.add_point(inference.point)

    def add_point(self, point: np.ndarray) -> None:
        if not self.points:
            self.points = [point.copy()]
            return
        start = self.points[-1]
        if np.linalg.norm(point - start) < 1e-6:
            return
        ents = self.document.active_entities
        closes = len(self.points) > 1 and np.linalg.norm(point - self.points[0]) < 1e-6
        a, b = self.local(start), self.local(point)
        if self.commit("Line", lambda: draw.line(ents, [a, b])):
            self.points.append(point.copy())
            self.lock = None
            self.lock_name = None
            if closes or self._made_face(point):
                self.points = []

    def _made_face(self, point: np.ndarray) -> bool:
        """SketchUp ends the chain when the new line closes a face."""
        ents = self.document.active_entities
        v = ents.find_vertex(self.local(point))
        return v is not None and len(v.edges) > 1 and any(e.faces for e in v.edges)

    def mouse_double_click(self, event: "QMouseEvent") -> bool:
        self.points = []
        return True

    def vcb_entered(self, text: str) -> bool:
        if not self.points:
            self.message = "Click a start point first"
            return False
        start = self.points[-1]
        try:
            stripped = text.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                self.add_point(np.array(parse_lengths(stripped[1:-1], self.units, 3)))
                return True
            if stripped.startswith("<") and stripped.endswith(">"):
                self.add_point(start + np.array(parse_lengths(stripped[1:-1], self.units, 3)))
                return True
            (length,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if self.current is None or np.linalg.norm(self.current.point - start) < 1e-9:
            self.message = "Move the mouse to show the direction first"
            return False
        direction = normalize(self.current.point - start)
        self.add_point(start + direction * length)
        return True

    def preview(self, painter: "QPainter") -> None:
        if self.points and self.current is not None:
            overlay.polyline(painter, self.viewport, [self.points[-1], self.current.point], overlay.RUBBER_BAND, 2.0)

    def hint_for_state(self) -> str:
        if not self.points:
            return "Click to start a line. Arrow keys lock to an axis; type a length and press Enter."
        length = ""
        if self.current is not None:
            length = f"Length {self.fmt(float(np.linalg.norm(self.current.point - self.points[-1])))}. "
        return f"{length}Click the next point, or type a length. Esc or double-click ends the line."


# ============================================================================ Rectangle


class RectangleTool(DrawingTool):
    name = "Rectangle"
    shortcut = "R"
    hint = "Click the first corner."
    vcb_label = "Dimensions"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.plane: Plane | None = None

    def reset(self) -> None:
        super().reset()
        self.plane = None

    def drawing_plane(self) -> Plane | None:
        return self.plane

    def infer(self, x: float, y: float, plane: Plane | None = None) -> Inference:
        # Rectangles are not drawn from a start point along an axis: no axis inference.
        saved, self.points = self.points, []
        try:
            return super().infer(x, y, plane)
        finally:
            self.points = saved

    def toggle_lock(self, axis: str) -> None:
        if self.points:
            self.plane = Plane.from_point_normal(self.points[0], AXES[axis])
            self.lock_name = f"plane normal to {axis.upper()}"
        else:
            super().toggle_lock(axis)

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.points = [inference.point.copy()]
            self.plane = _plane_from(inference, inference.point, self.lock_name if self.lock_name in AXES else None)
            self.lock = None
            return
        corner = self.corner()
        if corner is not None:
            self.create(corner)

    def corner(self) -> np.ndarray | None:
        return _on_plane(self, self.plane) if self.plane is not None else None

    def dims(self, corner: np.ndarray) -> tuple[float, float, draw.PlaneAxes]:
        assert self.plane is not None
        axes = _axes_for(self.plane)
        d = corner - self.points[0]
        return float(d @ axes[0]), float(d @ axes[1]), axes

    def create(self, corner: np.ndarray) -> None:
        width, depth, axes = self.dims(corner)
        if abs(width) < 1e-6 or abs(depth) < 1e-6:
            self.message = "The rectangle needs a width and a depth"
            return
        ents = self.document.active_entities
        origin, local_axes = self.local(self.points[0]), self.local_axes(axes)
        if self.commit("Rectangle", lambda: draw.rectangle(ents, origin, width, depth, local_axes)):
            self.reset()

    def vcb_entered(self, text: str) -> bool:
        if not self.points or self.plane is None:
            self.message = "Click the first corner first"
            return False
        try:
            width, depth = parse_lengths(text, self.units, 2)
        except VcbError as exc:
            self.message = str(exc)
            return False
        axes = _axes_for(self.plane)
        corner = self.corner()
        if corner is not None:  # keep the quadrant the mouse is in
            w0, d0, _ = self.dims(corner)
            width = math.copysign(width, w0 or 1.0)
            depth = math.copysign(depth, d0 or 1.0)
        self.create(self.points[0] + axes[0] * width + axes[1] * depth)
        return True

    def preview(self, painter: "QPainter") -> None:
        if not self.points or self.plane is None:
            return
        corner = self.corner()
        if corner is None:
            return
        w, d, (u, v, _) = self.dims(corner)
        o = self.points[0]
        overlay.polyline(painter, self.viewport, [o, o + u * w, o + u * w + v * d, o + v * d], overlay.PREVIEW, 2.0, closed=True)

    def hint_for_state(self) -> str:
        if not self.points:
            return "Click the first corner (on a face to draw on it). Arrow keys pick the plane."
        corner = self.corner()
        size = ""
        if corner is not None:
            w, d, _ = self.dims(corner)
            size = f"{self.fmt(abs(w))}, {self.fmt(abs(d))}. "
        return f"{size}Click the opposite corner, or type width,depth and press Enter."


# ============================================================================ Circle & Polygon


class _CenteredTool(DrawingTool):
    """Shapes drawn from a centre and a radius (circle, polygon)."""

    vcb_label = "Radius"
    default_count = 24
    count_word = "segments"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.plane: Plane | None = None
        self.count = self.default_count

    def reset(self) -> None:
        super().reset()
        self.plane = None

    def drawing_plane(self) -> Plane | None:
        return self.plane

    def infer(self, x: float, y: float, plane: Plane | None = None) -> Inference:
        saved, self.points = self.points, []
        try:
            return super().infer(x, y, plane)
        finally:
            self.points = saved

    def toggle_lock(self, axis: str) -> None:
        self.lock_name = None if self.lock_name == axis else axis
        if self.points and self.lock_name:
            self.plane = Plane.from_point_normal(self.points[0], AXES[axis])

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.points = [inference.point.copy()]
            self.plane = _plane_from(inference, inference.point, self.lock_name)
            return
        radius = self.radius()
        if radius:
            self.create(radius)

    def radius(self) -> float:
        if not self.points or self.plane is None:
            return 0.0
        p = _on_plane(self, self.plane)
        return 0.0 if p is None else float(np.linalg.norm(p - self.points[0]))

    def create(self, radius: float) -> None:
        if radius < 1e-6:
            return
        assert self.plane is not None
        ents = self.document.active_entities
        center, axes, count = self.local(self.points[0]), self.local_axes(_axes_for(self.plane)), self.count
        if self.commit(self.name, lambda: self.build(ents, center, radius, count, axes)):
            self.reset()

    def build(self, ents, center, radius, count, axes):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def outline(self, radius: float) -> list[np.ndarray]:
        assert self.plane is not None
        u, v, _ = _axes_for(self.plane)
        c = self.points[0]
        return [c + radius * (math.cos(2 * math.pi * k / self.count) * u + math.sin(2 * math.pi * k / self.count) * v)
                for k in range(self.count)]

    def vcb_entered(self, text: str) -> bool:
        try:
            count = parse_segments(text)
            if count is not None:
                self.count = count
                return True
            (radius,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if not self.points:
            self.message = "Click the centre first"
            return False
        self.create(radius)
        return True

    def preview(self, painter: "QPainter") -> None:
        radius = self.radius()
        if radius > 0:
            overlay.polyline(painter, self.viewport, self.outline(radius), overlay.PREVIEW, 2.0, closed=True)
            overlay.polyline(painter, self.viewport, [self.points[0], self.outline(radius)[0]], overlay.PREVIEW, 1.0, dashed=True)

    def hint_for_state(self) -> str:
        if not self.points:
            return f"Click the centre. Type e.g. 32s for {self.count} -> 32 {self.count_word}."
        return (f"Radius {self.fmt(self.radius())}, {self.count} {self.count_word}. "
                "Click to set the radius, or type it and press Enter.")


class CircleTool(_CenteredTool):
    name = "Circle"
    shortcut = "C"
    hint = "Click the centre of the circle."

    def build(self, ents, center, radius, count, axes):  # type: ignore[no-untyped-def]
        return draw.circle(ents, center, radius, count, axes)


class PolygonTool(_CenteredTool):
    name = "Polygon"
    shortcut = ""
    hint = "Click the centre of the polygon."
    default_count = 6
    count_word = "sides"

    def build(self, ents, center, radius, count, axes):  # type: ignore[no-untyped-def]
        return draw.polygon(ents, center, radius, count, axes)


# ============================================================================ Arc


class ArcTool(DrawingTool):
    """SketchUp's 2-point arc: click the start, the end, then pull out the bulge."""

    name = "Arc"
    shortcut = "A"
    hint = "Click the start of the arc."
    vcb_label = "Bulge"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.plane: Plane | None = None
        self.segments = 12

    def reset(self) -> None:
        super().reset()
        self.plane = None

    def drawing_plane(self) -> Plane | None:
        return self.plane

    def click(self, inference: Inference) -> None:
        if not self.points:
            self.points = [inference.point.copy()]
            self.plane = _plane_from(inference, inference.point)
            return
        assert self.plane is not None
        if len(self.points) == 1:
            end = self.plane.project(inference.point)
            if np.linalg.norm(end - self.points[0]) > 1e-6:
                self.points.append(end)
            return
        bulge = self.bulge()
        if bulge is not None:
            self.create(bulge)

    def chord(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Start, end and the in-plane direction the bulge is measured along."""
        assert self.plane is not None and len(self.points) == 2
        s, e = self.points
        return s, e, normalize(np.cross(self.plane.normal, e - s))

    def bulge(self) -> float | None:
        if len(self.points) < 2 or self.plane is None:
            return None
        p = _on_plane(self, self.plane)
        if p is None:
            return None
        s, e, perp = self.chord()
        return float((p - (s + e) / 2) @ perp)

    def arc_points(self, bulge: float) -> tuple[np.ndarray, float, draw.PlaneAxes, float] | None:
        """Centre, radius, axes and sweep (degrees) of the arc through start, bulge, end."""
        if abs(bulge) < 1e-6 or self.plane is None:
            return None
        s, e, perp = self.chord()
        half = float(np.linalg.norm(e - s)) / 2
        mid = (s + e) / 2
        t = (bulge * bulge - half * half) / (2 * bulge)
        centre = mid + perp * t
        radius = abs(t - bulge)
        n = self.plane.normal
        u = normalize(s - centre)
        v = np.cross(n, u)
        top = mid + perp * bulge

        def angle(p: np.ndarray) -> float:
            d = p - centre
            return math.atan2(float(d @ v), float(d @ u)) % (2 * math.pi)

        a_end, a_mid = angle(e), angle(top)
        sweep = a_end if a_mid < a_end else a_end - 2 * math.pi
        return centre, radius, (u, v, n), math.degrees(sweep)

    def create(self, bulge: float) -> None:
        arc = self.arc_points(bulge)
        if arc is None:
            self.message = "Pull the arc out from its chord"
            return
        centre, radius, axes, sweep = arc
        centre, axes = self.local(centre), self.local_axes(axes)
        ents, segments = self.document.active_entities, self.segments
        if self.commit("Arc", lambda: draw.arc(ents, centre, radius, 0.0, sweep, segments, axes)):
            self.reset()

    def vcb_entered(self, text: str) -> bool:
        try:
            count = parse_segments(text)
            if count is not None:
                self.segments = count
                return True
            (bulge,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if len(self.points) < 2:
            self.message = "Click the start and end of the arc first"
            return False
        current = self.bulge()
        self.create(math.copysign(bulge, current if current else 1.0))
        return True

    def preview(self, painter: "QPainter") -> None:
        if len(self.points) == 1 and self.current is not None:
            overlay.polyline(painter, self.viewport, [self.points[0], self.current.point], overlay.RUBBER_BAND, 1.5)
        if len(self.points) == 2:
            bulge = self.bulge()
            arc = self.arc_points(bulge) if bulge else None
            if arc is None:
                overlay.polyline(painter, self.viewport, self.points, overlay.RUBBER_BAND, 1.5)
                return
            centre, radius, (u, v, _), sweep = arc
            pts = [centre + radius * (math.cos(math.radians(sweep) * k / 48) * u + math.sin(math.radians(sweep) * k / 48) * v)
                   for k in range(49)]
            overlay.polyline(painter, self.viewport, pts, overlay.PREVIEW, 2.0)

    def hint_for_state(self) -> str:
        if not self.points:
            return f"Click the start of the arc ({self.segments} segments; type e.g. 24s to change)."
        if len(self.points) == 1:
            return "Click the end of the arc."
        bulge = self.bulge() or 0.0
        return f"Bulge {self.fmt(abs(bulge))}. Click to finish, or type the bulge and press Enter."
