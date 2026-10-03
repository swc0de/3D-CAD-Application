"""Camera navigation math (orbit, pan, zoom to cursor) with no Qt dependency.

All functions mutate a :class:`~pymodeler.render.camera.Camera` in place.  Screen
coordinates are logical pixels with the origin at the top-left of the viewport.
"""

from __future__ import annotations

import math

import numpy as np

from pymodeler.core.transform import apply_vector, rotation
from pymodeler.core.vec import normalize
from pymodeler.render.camera import Camera

ORBIT_DEGREES_PER_PIXEL = 0.4
WHEEL_ZOOM_FACTOR = 0.85
"""Distance multiplier per wheel notch toward the cursor."""
MIN_DISTANCE = 1.0
MAX_PITCH = 89.0


def pixel_ray(camera: Camera, x: float, y: float, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """World-space ray ``(origin, direction)`` through a pixel."""
    right, up, back = camera.basis()
    nx = 2.0 * x / max(width, 1) - 1.0
    ny = 1.0 - 2.0 * y / max(height, 1)
    aspect = width / max(height, 1)
    if camera.perspective:
        t = math.tan(math.radians(camera.fov) / 2.0)
        direction = normalize(-back + right * nx * t * aspect + up * ny * t)
        return camera.eye.copy(), direction
    half_h = camera.ortho_height / 2.0
    origin = camera.eye + right * nx * half_h * aspect + up * ny * half_h
    return origin, -back


def pixel_to_target_plane(camera: Camera, x: float, y: float, width: int, height: int) -> np.ndarray:
    """Where a pixel's ray meets the plane through the target facing the camera."""
    origin, direction = pixel_ray(camera, x, y, width, height)
    _, _, back = camera.basis()
    denom = float(direction @ -back)
    if abs(denom) < 1e-12:
        return camera.target.copy()
    t = float((camera.target - origin) @ -back) / denom
    return origin + direction * t


def world_per_pixel(camera: Camera, height: int) -> float:
    """World units covered by one pixel at the target's depth."""
    if camera.perspective:
        return 2.0 * camera.distance() * math.tan(math.radians(camera.fov) / 2.0) / max(height, 1)
    return camera.ortho_height / max(height, 1)


def orbit(camera: Camera, dx: float, dy: float, pivot: np.ndarray | None = None) -> None:
    """Orbit around ``pivot`` (default: the target): ``dx`` turns about world Z,
    ``dy`` tilts up/down, both in pixels."""
    pivot = camera.target.copy() if pivot is None else np.asarray(pivot, dtype=float)
    yaw = -dx * ORBIT_DEGREES_PER_PIXEL
    pitch = -dy * ORBIT_DEGREES_PER_PIXEL
    right, _, back = camera.basis()
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, float(back[2])))))
    # A positive pitch about the camera's right axis lowers the eye; keep it off the poles.
    pitch = max(elevation - MAX_PITCH, min(elevation + MAX_PITCH, pitch))
    m = rotation((0, 0, 1), yaw, pivot) @ rotation(right, pitch, pivot)
    camera.eye = (m @ np.append(camera.eye, 1.0))[:3]
    camera.target = (m @ np.append(camera.target, 1.0))[:3]
    camera.up = np.array([0.0, 0.0, 1.0]) if abs(camera.basis()[2][2]) < 0.999 else apply_vector(m, camera.up)


def pan(camera: Camera, dx: float, dy: float, height: int) -> None:
    """Slide the view so the scene follows the mouse by ``dx``, ``dy`` pixels."""
    right, up, _ = camera.basis()
    scale = world_per_pixel(camera, height)
    shift = (-right * dx + up * dy) * scale
    camera.eye = camera.eye + shift
    camera.target = camera.target + shift


def zoom(camera: Camera, factor: float, x: float, y: float, width: int, height: int) -> None:
    """Zoom by ``factor`` (< 1 zooms in) keeping the point under the cursor fixed."""
    anchor = pixel_to_target_plane(camera, x, y, width, height)
    if camera.perspective:
        new_eye = anchor + (camera.eye - anchor) * factor
        new_target = anchor + (camera.target - anchor) * factor
        if np.linalg.norm(new_eye - new_target) < MIN_DISTANCE:
            return
        camera.eye, camera.target = new_eye, new_target
    else:
        camera.ortho_height = max(camera.ortho_height * factor, 1.0)
        new_target = anchor + (camera.target - anchor) * factor
        camera.eye = camera.eye + (new_target - camera.target)
        camera.target = new_target


def wheel_factor(notches: float) -> float:
    """Zoom factor for a number of wheel notches (positive = toward the screen)."""
    return WHEEL_ZOOM_FACTOR ** notches


def set_perspective(camera: Camera, perspective: bool) -> None:
    """Switch projection while keeping the target's apparent size."""
    if camera.perspective == perspective:
        return
    t = math.tan(math.radians(camera.fov) / 2.0)
    if perspective:
        distance = max(camera.ortho_height / (2.0 * t), MIN_DISTANCE)
        _, _, back = camera.basis()
        camera.eye = camera.target + back * distance
    else:
        camera.ortho_height = 2.0 * camera.distance() * t
    camera.perspective = perspective
