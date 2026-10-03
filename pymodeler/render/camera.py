"""Cameras, standard views and zoom-extents framing (no GUI dependencies)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.vec import normalize

STANDARD_VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "iso": ((1.0, -1.25, 0.95), (0.0, 0.0, 1.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
}
"""Direction from the target to the eye, and the up vector, for each standard view."""


@dataclass
class Camera:
    """A look-at camera with perspective or parallel projection."""

    eye: np.ndarray = field(default_factory=lambda: np.array([3000.0, -4000.0, 3000.0]))
    target: np.ndarray = field(default_factory=lambda: np.zeros(3))
    up: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 1.0]))
    fov: float = 30.0
    """Vertical field of view in degrees (perspective)."""
    perspective: bool = True
    ortho_height: float = 4000.0
    """Height of the visible area in millimetres (parallel projection)."""

    # -- matrices ---------------------------------------------------------------------
    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Camera ``(right, up, back)`` unit vectors (``back`` points toward the eye)."""
        back = normalize(self.eye - self.target)
        up = self.up
        if abs(float(np.dot(normalize(up), back))) > 0.999:
            up = np.array([0.0, 1.0, 0.0]) if abs(back[2]) > 0.9 else np.array([0.0, 0.0, 1.0])
        right = normalize(np.cross(up, back))
        return right, np.cross(back, right), back

    def view_matrix(self) -> np.ndarray:
        """World-to-camera matrix."""
        right, up, back = self.basis()
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = right, up, back
        m[:3, 3] = -m[:3, :3] @ self.eye
        return m

    def distance(self) -> float:
        """Eye-to-target distance."""
        return float(np.linalg.norm(self.eye - self.target))

    def clip_range(self, radius: float) -> tuple[float, float]:
        """Near and far planes that comfortably contain a scene of ``radius``."""
        d = self.distance()
        far = d + radius * 2.0 + 1.0
        near = max(far * 1e-4, d - radius * 2.0, 1.0) if self.perspective else -far
        return near, far

    def projection_matrix(self, aspect: float, near: float, far: float) -> np.ndarray:
        """OpenGL-style projection matrix (clip space z in -1..1)."""
        m = np.zeros((4, 4))
        if self.perspective:
            f = 1.0 / math.tan(math.radians(self.fov) / 2.0)
            m[0, 0] = f / aspect
            m[1, 1] = f
            m[2, 2] = (far + near) / (near - far)
            m[2, 3] = 2.0 * far * near / (near - far)
            m[3, 2] = -1.0
        else:
            h = self.ortho_height / 2.0
            w = h * aspect
            m[0, 0] = 1.0 / w
            m[1, 1] = 1.0 / h
            m[2, 2] = -2.0 / (far - near)
            m[2, 3] = -(far + near) / (far - near)
            m[3, 3] = 1.0
        return m

    # -- framing ----------------------------------------------------------------------
    def frame(self, lo: np.ndarray, hi: np.ndarray, aspect: float, margin: float = 1.08) -> None:
        """Zoom extents: keep the view direction, fit the box ``lo..hi`` in view."""
        center = (np.asarray(lo, float) + np.asarray(hi, float)) / 2.0
        size = np.maximum(np.asarray(hi, float) - np.asarray(lo, float), 1e-6)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        direction = normalize(self.eye - self.target)
        self.target = center
        right, up, back = self.basis()
        rel = corners - center
        xs, ys, zs = rel @ right, rel @ up, rel @ back
        if self.perspective:
            ty = math.tan(math.radians(self.fov) / 2.0)
            tx = ty * aspect
            need = max(
                float(np.max(zs + np.abs(xs) * margin / tx)),
                float(np.max(zs + np.abs(ys) * margin / ty)),
            )
            self.eye = center + direction * max(need, float(np.max(size)) * 0.1)
        else:
            width = 2 * float(np.max(np.abs(xs)))
            height = 2 * float(np.max(np.abs(ys)))
            self.ortho_height = max(height, width / aspect, 1e-3) * margin
            self.eye = center + direction * (float(np.linalg.norm(size)) * 2.0 + 1000.0)

    @staticmethod
    def standard(
        view: str,
        bounds: tuple[np.ndarray, np.ndarray] | None,
        aspect: float,
        perspective: bool | None = None,
    ) -> "Camera":
        """A camera for a standard view, framed on ``bounds``.

        ``perspective`` defaults to on for ``iso`` and off (parallel) for the axis views.

        Raises:
            ValueError: on an unknown view name.
        """
        if view not in STANDARD_VIEWS:
            raise ValueError(f"unknown view {view!r} (use {', '.join(STANDARD_VIEWS)})")
        direction, up = STANDARD_VIEWS[view]
        persp = (view == "iso") if perspective is None else perspective
        cam = Camera(
            eye=np.array(direction) * 10000.0,
            target=np.zeros(3),
            up=np.array(up),
            perspective=persp,
        )
        lo, hi = bounds if bounds is not None else (np.full(3, -1000.0), np.full(3, 1000.0))
        cam.frame(np.asarray(lo, float), np.asarray(hi, float), aspect)
        return cam
