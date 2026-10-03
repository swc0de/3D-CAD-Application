"""The 3D viewport: a QOpenGLWidget drawing the document with moderngl.

Navigation follows SketchUp: middle-drag orbits, Shift+middle-drag pans, the wheel
zooms toward the cursor.  Left-button and key events go to the active tool, which can
also draw 2D overlays on top of the 3D view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from pymodeler.render.camera import STANDARD_VIEWS, Camera
from pymodeler.render.scene import SceneData, build_scene
from pymodeler.ui import navigation

if TYPE_CHECKING:
    from pymodeler.ui.document import Document
    from pymodeler.ui.tools.base import Tool


class Viewport(QOpenGLWidget):
    """Interactive OpenGL view of a :class:`~pymodeler.ui.document.Document`."""

    def __init__(self, document: "Document", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.document = document
        document.listeners.append(self._on_document_changed)
        self.camera = Camera.standard("iso", None, 4 / 3)
        self.view_name = "iso"
        self.perspective = True
        self.show_grid = False
        self.show_axes = True
        self.tool: Tool | None = None
        self.on_tool_status: Callable[[str], None] | None = None
        self.gl_error: str | None = None
        self._ctx = None
        self._renderer = None
        self._scene: SceneData | None = None
        self._scene_dirty = True
        self._nav: tuple[str, QPointF] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)

    # ------------------------------------------------------------------ document & view
    def _on_document_changed(self, _document: "Document") -> None:
        self._scene_dirty = True
        self.update()

    def refresh(self) -> None:
        """Rebuild the scene from the model on the next paint."""
        self._scene_dirty = True
        self.update()

    def scene(self) -> SceneData:
        """The current scene data (rebuilt lazily)."""
        if self._scene is None or self._scene_dirty:
            axes: bool | str = "long" if self.show_axes else False
            self._scene = build_scene(self.document.model, axes=axes, grid=self.show_grid)
            self._scene_dirty = False
            if self._renderer is not None:
                self._renderer.set_scene(self._scene)
        return self._scene

    def aspect(self) -> float:
        return self.width() / max(self.height(), 1)

    def zoom_extents(self) -> None:
        """Fit the whole model in view (Shift+Z)."""
        bounds = self.document.model.bounds()
        if bounds is None:
            bounds = (np.array([-2000.0, -2000.0, 0.0]), np.array([2000.0, 2000.0, 2000.0]))
        self.camera.frame(bounds[0], bounds[1], self.aspect())
        self.update()

    def set_view(self, name: str) -> None:
        """Switch to a standard view (iso, top, front, right, back, left, bottom)."""
        if name not in STANDARD_VIEWS:
            raise ValueError(f"unknown view {name!r}")
        self.view_name = name
        self.camera = Camera.standard(name, self.document.model.bounds(), self.aspect(), self.perspective)
        self.update()

    def set_perspective(self, on: bool) -> None:
        """Toggle perspective / parallel projection."""
        self.perspective = on
        navigation.set_perspective(self.camera, on)
        self.update()

    def set_tool(self, tool: "Tool") -> None:
        """Make ``tool`` the active tool."""
        if self.tool is not None:
            self.tool.deactivate()
        self.tool = tool
        tool.activate()
        self._emit_status()
        self.update()

    def _emit_status(self) -> None:
        if self.on_tool_status is not None and self.tool is not None:
            self.on_tool_status(self.tool.status())

    @staticmethod
    def shift_held() -> bool:
        return bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

    # ------------------------------------------------------------------ GL
    def initializeGL(self) -> None:
        try:
            from pymodeler.render.gl_renderer import GLSceneRenderer

            self._ctx = attach_moderngl()
            self._renderer = GLSceneRenderer(self._ctx)
            self._scene_dirty = True
        except Exception as exc:  # noqa: BLE001 - show the problem instead of crashing
            self.gl_error = f"OpenGL 3.3 is not available: {exc}"
            self._ctx = self._renderer = None

    def paintGL(self) -> None:
        if self._ctx is None or self._renderer is None:
            self._paint_message(self.gl_error or "OpenGL is not available")
            return
        dpr = self.devicePixelRatioF()
        width, height = int(self.width() * dpr), int(self.height() * dpr)
        self.scene()
        fbo = self._ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        self._renderer.draw(self.camera, width, height, horizon=True)
        self._ctx.disable(self._ctx.DEPTH_TEST | self._ctx.CULL_FACE | self._ctx.BLEND)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_view_label(painter)
        if self.tool is not None:
            self.tool.draw_overlay(painter)
        painter.end()

    def _paint_message(self, text: str) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(200, 210, 225))
        painter.setPen(QColor(40, 40, 40))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def _paint_view_label(self, painter: QPainter) -> None:
        label = self.view_name.capitalize() + ("" if self.camera.perspective else "  (parallel)")
        painter.setFont(QFont(painter.font().family(), 9))
        painter.setPen(QColor(40, 45, 55))
        painter.drawText(10, 18, label)

    # ------------------------------------------------------------------ projection helpers
    def project(self, point: np.ndarray) -> QPointF | None:
        """Screen position (logical pixels) of a world point, or None if behind the eye."""
        near, far = self.camera.clip_range(self.scene().radius)
        mvp = self.camera.projection_matrix(self.aspect(), near, far) @ self.camera.view_matrix()
        clip = mvp @ np.append(np.asarray(point, dtype=float), 1.0)
        if clip[3] <= 1e-9:
            return None
        ndc = clip[:3] / clip[3]
        return QPointF((ndc[0] + 1.0) * 0.5 * self.width(), (1.0 - ndc[1]) * 0.5 * self.height())

    def ray(self, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
        """World ray through a pixel."""
        return navigation.pixel_ray(self.camera, x, y, self.width(), self.height())

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            mode = "pan" if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else "orbit"
            self._nav = (mode, event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor if mode == "pan" else Qt.CursorShape.SizeAllCursor)
            return
        if self.tool is not None and self.tool.mouse_press(event):
            self._emit_status()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._nav is not None:
            mode, last = self._nav
            pos = event.position()
            dx, dy = pos.x() - last.x(), pos.y() - last.y()
            if mode == "pan":
                navigation.pan(self.camera, dx, dy, self.height())
            else:
                navigation.orbit(self.camera, dx, dy)
            self._nav = (mode, pos)
            self.update()
            return
        if self.tool is not None and self.tool.mouse_move(event):
            self._emit_status()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._nav is not None:
            self._nav = None
            self.unsetCursor()
            return
        if self.tool is not None and self.tool.mouse_release(event):
            self._emit_status()
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            # SketchUp: double-click the wheel to centre the view on that point.
            pos = event.position()
            point = navigation.pixel_to_target_plane(self.camera, pos.x(), pos.y(), self.width(), self.height())
            shift = point - self.camera.target
            self.camera.target = self.camera.target + shift
            self.camera.eye = self.camera.eye + shift
            self.update()
            return
        if self.tool is not None and self.tool.mouse_double_click(event):
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        notches = event.angleDelta().y() / 120.0
        if notches == 0:
            return
        pos = event.position()
        navigation.zoom(self.camera, navigation.wheel_factor(notches), pos.x(), pos.y(), self.width(), self.height())
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.tool is not None:
            self.tool.reset()
            self._emit_status()
            self.update()
            return
        if self.tool is not None and self.tool.key_press(event):
            self._emit_status()
            self.update()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.update()


def attach_moderngl() -> object:
    """A moderngl context wrapping the widget's current OpenGL context.

    Linux systems without development packages lack the unversioned ``libGL.so`` that
    moderngl looks for by default, and Qt may use EGL rather than GLX, so try those too.
    """
    import moderngl

    attempts: list[dict[str, str]] = [
        {},
        {"libgl": "libGL.so.1", "libx11": "libX11.so.6"},
        {"backend": "egl", "libgl": "libGL.so.1", "libegl": "libEGL.so.1"},
    ]
    errors = []
    for kwargs in attempts:
        try:
            return moderngl.create_context(require=330, **kwargs)
        except Exception as exc:  # noqa: BLE001 - try the next way of attaching
            errors.append(str(exc))
    raise RuntimeError("; ".join(dict.fromkeys(errors)))
