"""2D overlay drawing helpers (inference markers, rubber bands, labels)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

if TYPE_CHECKING:
    from pymodeler.ui.inference import Inference
    from pymodeler.ui.viewport import Viewport

RUBBER_BAND = QColor(20, 20, 20)
PREVIEW = QColor(30, 30, 30)


def polyline(painter: QPainter, viewport: "Viewport", points: Iterable[np.ndarray], color: QColor = PREVIEW,
             width: float = 1.5, closed: bool = False, dashed: bool = False) -> None:
    """Draw world-space points as a connected screen-space line."""
    projected = [viewport.project(p) for p in points]
    if any(p is None for p in projected) or len(projected) < 2:
        return
    pen = QPen(color, width)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    poly = QPolygonF([p for p in projected if p is not None])
    if closed:
        painter.drawPolygon(poly)
    else:
        painter.drawPolyline(poly)


def marker(painter: QPainter, viewport: "Viewport", inference: "Inference") -> None:
    """Draw the snap marker and guide line for an inference."""
    color = QColor(*inference.color)
    if inference.guide is not None:
        polyline(painter, viewport, inference.guide, color, 2.0, dashed=inference.kind in ("parallel", "perpendicular"))
    pos = viewport.project(inference.point)
    if pos is None or inference.kind == "plane":
        return
    painter.setPen(QPen(QColor(255, 255, 255), 1.0))
    painter.setBrush(QBrush(color))
    r = 5.5
    if inference.kind in ("endpoint", "origin"):
        painter.drawEllipse(pos, r, r)
    elif inference.kind == "midpoint":
        painter.drawEllipse(pos, r, r)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(pos, 2.0, 2.0)
    elif inference.kind == "on_edge":
        painter.drawRect(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r))
    elif inference.kind == "on_face":
        painter.drawPolygon(QPolygonF([QPointF(pos.x(), pos.y() - r - 1), QPointF(pos.x() + r + 1, pos.y()),
                                       QPointF(pos.x(), pos.y() + r + 1), QPointF(pos.x() - r - 1, pos.y())]))
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 2.0))
        painter.drawEllipse(pos, r, r)


def label(painter: QPainter, pos: QPointF, text: str) -> None:
    """A small tooltip-style label next to the cursor."""
    if not text:
        return
    painter.setFont(QFont(painter.font().family(), 9))
    metrics = painter.fontMetrics()
    rect = QRectF(pos.x() + 14, pos.y() + 14, metrics.horizontalAdvance(text) + 10, metrics.height() + 4)
    painter.setPen(QPen(QColor(120, 120, 120), 1.0))
    painter.setBrush(QBrush(QColor(255, 255, 225)))
    painter.drawRect(rect)
    painter.setPen(QColor(20, 20, 20))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
