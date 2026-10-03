"""The Tape Measure tool (T): measure distances, place guides, resize the model."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from pymodeler.core.entities import Edge
from pymodeler.core.transform import apply_point, scaling
from pymodeler.core.vec import normalize
from pymodeler.ops.transform import transform_entities
from pymodeler.ui import overlay
from pymodeler.ui.inference import Inference
from pymodeler.ui.tools.draw_tools import DrawingTool
from pymodeler.ui.vcb import VcbError, parse_lengths

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QPainter

POINT_KINDS = ("endpoint", "midpoint", "origin", "guide_point")


class TapeTool(DrawingTool):
    """SketchUp's Tape Measure.

    Click two points to measure the distance between them. Starting on an edge (or a
    guide line) measures the perpendicular distance from it and leaves a parallel guide
    line; starting on a point (endpoint, midpoint, origin or guide point) leaves a guide
    point. Ctrl switches guide creation off and on. After measuring, typing a new length
    resizes the model (or the open group) so the measured distance becomes that length.
    """

    name = "Tape Measure"
    shortcut = "T"
    hint = "Click a start point."
    vcb_label = "Length"

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self.make_guides = True
        self.from_line: tuple[np.ndarray, np.ndarray] | None = None
        """(point, unit direction) of the edge or guide the measurement started on."""
        self.start_is_point = False
        self.measured: tuple[np.ndarray, np.ndarray] | None = None
        """The last completed measurement (start, end), for resizing."""

    def reset(self) -> None:
        super().reset()
        self.from_line = None
        self.start_is_point = False

    def busy(self) -> bool:
        return bool(self.points)

    # -- measuring -------------------------------------------------------------------------
    def click(self, inference: Inference) -> None:
        if not self.points:
            self.points = [inference.point.copy()]
            self.from_line = _line_of(inference)
            self.start_is_point = inference.kind in POINT_KINDS
            self.measured = None
            return
        self.finish(self.end_point(inference.point))

    def end_point(self, point: np.ndarray) -> np.ndarray:
        """Where the measurement ends: measuring from a line, the perpendicular offset."""
        start = self.points[0]
        if self.from_line is None:
            return point
        d = self.from_line[1]
        offset = point - start
        return start + offset - d * float(offset @ d)

    def finish(self, end: np.ndarray) -> bool:
        """Complete the measurement at ``end`` (making a guide if enabled)."""
        start = self.points[0]
        length = float(np.linalg.norm(end - start))
        if length < 1e-6:
            return False
        model = self.document.model
        if self.make_guides and self.from_line is not None:
            direction = self.from_line[1]
            self.commit("Guide", lambda: model.add_guide(end, direction))
        elif self.make_guides and self.start_is_point:
            self.commit("Guide Point", lambda: model.add_guide(end))
        self.measured = (start.copy(), end.copy())
        self.points = []
        self.from_line = None
        self.message = f"Distance {self.fmt(length)}."
        return True

    def vcb_entered(self, text: str) -> bool:
        try:
            (length,) = parse_lengths(text, self.units, 1)
        except VcbError as exc:
            self.message = str(exc)
            return False
        if self.points:
            start = self.points[0]
            end = self.end_point(self.current.point) if self.current is not None else start
            if np.linalg.norm(end - start) < 1e-9:
                self.message = "Move the mouse to show the direction first"
                return False
            return self.finish(start + normalize(end - start) * length)
        if self.measured is not None:
            old = float(np.linalg.norm(self.measured[1] - self.measured[0]))
            if length <= 0:
                self.message = "The new length must be positive"
                return False
            factor = length / old
            if self.confirm_resize(factor):
                self.resize(factor)
            return True
        self.message = "Measure a distance first, then type a new length to resize"
        return False

    def resize(self, factor: float) -> bool:
        """Scale the model (or the open group's contents) uniformly about its origin."""
        doc = self.document
        active = doc.active_entities
        items = [*active.faces.values(), *active.edges.values(), *active.instances.values()]
        model, at_root = doc.model, not doc.edit_path
        matrix = scaling(factor)

        def action() -> None:
            transform_entities(active, items, matrix)
            if at_root:
                model.guides = [g.transformed(matrix) for g in model.guides]

        ok = self.commit("Resize", action)
        if ok:
            self.measured = None
            self.message = f"Resized by {factor:.4g}x."
        return ok

    def confirm_resize(self, factor: float) -> bool:
        """Ask before resizing (tests replace this)."""
        what = "the open group" if self.document.edit_path else "the model"
        answer = QMessageBox.question(self.viewport, "Resize", f"Resize {what} by {factor:.4g}x?")
        return answer == QMessageBox.StandardButton.Yes

    def key_press(self, event: "QKeyEvent") -> bool:
        if event.key() == Qt.Key.Key_Control:
            self.make_guides = not self.make_guides
            return True
        return super().key_press(event)

    # -- display -----------------------------------------------------------------------------
    def preview(self, painter: "QPainter") -> None:
        if not self.points or self.current is None:
            return
        start, end = self.points[0], self.end_point(self.current.point)
        overlay.polyline(painter, self.viewport, [start, end], overlay.RUBBER_BAND, 1.5, dashed=True)
        if self.make_guides and self.from_line is not None:
            reach = self.viewport.scene().radius * 2
            d = self.from_line[1]
            overlay.polyline(painter, self.viewport, [end - d * reach, end + d * reach], overlay.PREVIEW, 1.0,
                             dashed=True)

    def draw_overlay(self, painter: "QPainter") -> None:
        self.preview(painter)
        if self.current is None:
            return
        overlay.marker(painter, self.viewport, self.current)
        text = self.current.label
        if self.points:
            length = float(np.linalg.norm(self.end_point(self.current.point) - self.points[0]))
            text = f"{text}  {self.fmt(length)}".strip()
        overlay.label(painter, self.cursor, text)

    def hint_for_state(self) -> str:
        guides = "on" if self.make_guides else "off"
        if self.points:
            length = ""
            if self.current is not None:
                length = f"Length {self.fmt(float(np.linalg.norm(self.end_point(self.current.point) - self.points[0])))}. "
            return f"{length}Click the end point, or type a length."
        if self.measured is not None:
            return "Type a new length to resize the model, or click to measure again."
        return (f"Click a start point. Start on an edge for a parallel guide, on a point for a guide point. "
                f"Ctrl: guides {guides}.")


def _line_of(inference: Inference) -> tuple[np.ndarray, np.ndarray] | None:
    """The world line under an inference (an edge or a guide line), if any."""
    if inference.kind == "on_edge" and inference.hit is not None and isinstance(inference.hit.entity, Edge):
        edge, world = inference.hit.entity, inference.hit.world
        a, b = apply_point(world, edge.v1.position), apply_point(world, edge.v2.position)
        return a, normalize(b - a)
    guide = inference.extra.get("guide")
    if inference.kind == "on_guide" and guide is not None and guide.direction is not None:
        return guide.point.copy(), guide.direction.copy()
    return None
