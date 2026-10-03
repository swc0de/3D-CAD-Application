"""Camera tools: Orbit (O), Pan (H) and Zoom (Z)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from pymodeler.ui import navigation
from pymodeler.ui.tools.base import Tool

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent


class _DragTool(Tool):
    """A tool that does something while the left button is dragged."""

    def __init__(self, viewport) -> None:  # type: ignore[no-untyped-def]
        super().__init__(viewport)
        self._last: tuple[float, float] | None = None
        self._start: tuple[float, float] | None = None

    def mouse_press(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        p = event.position()
        self._last = self._start = (p.x(), p.y())
        return True

    def mouse_move(self, event: "QMouseEvent") -> bool:
        if self._last is None:
            return False
        p = event.position()
        dx, dy = p.x() - self._last[0], p.y() - self._last[1]
        self._last = (p.x(), p.y())
        self.drag(dx, dy)
        self.viewport.update()
        return True

    def mouse_release(self, event: "QMouseEvent") -> bool:
        if event.button() != Qt.MouseButton.LeftButton or self._last is None:
            return False
        self._last = self._start = None
        return True

    def drag(self, dx: float, dy: float) -> None:
        """Handle a drag step of ``dx``, ``dy`` pixels."""


class OrbitTool(_DragTool):
    name = "Orbit"
    shortcut = "O"
    hint = "Drag to orbit. Shift+drag pans. Scroll to zoom."

    def drag(self, dx: float, dy: float) -> None:
        if self.viewport.shift_held():
            navigation.pan(self.viewport.camera, dx, dy, self.viewport.height())
        else:
            navigation.orbit(self.viewport.camera, dx, dy)


class PanTool(_DragTool):
    name = "Pan"
    shortcut = "H"
    hint = "Drag to pan the view. Scroll to zoom."

    def drag(self, dx: float, dy: float) -> None:
        navigation.pan(self.viewport.camera, dx, dy, self.viewport.height())


class ZoomTool(_DragTool):
    name = "Zoom"
    shortcut = "Z"
    hint = "Drag up to zoom in, down to zoom out (toward where you clicked). Shift+Z: zoom extents."

    def drag(self, dx: float, dy: float) -> None:
        if self._start is None:
            return
        factor = navigation.wheel_factor(-dy / 25.0)
        x, y = self._start
        navigation.zoom(self.viewport.camera, factor, x, y, self.viewport.width(), self.viewport.height())
