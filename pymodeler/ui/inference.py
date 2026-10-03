"""SketchUp-style inference: where should the cursor's 3D point be? (no Qt dependency)

Given the cursor position and, while drawing, the previous point, the engine returns
the best snap in SketchUp's priority order:

1. a locked axis or direction (arrow keys / Shift);
2. points: endpoints, midpoints and the origin;
3. axis inference from the previous point (red/green/blue lines);
4. parallel / perpendicular to a reference edge;
5. a point on an edge;
6. a point on a face;
7. the drawing plane (the ground, or the plane of the face drawing started on).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.entities import Edge, Face
from pymodeler.core.vec import Plane, normalize
from pymodeler.render.camera import Camera
from pymodeler.ui.navigation import pixel_ray
from pymodeler.ui.picking import Hit, PickScene, closest_point_on_line_to_ray

AXES = {"x": np.array([1.0, 0.0, 0.0]), "y": np.array([0.0, 1.0, 0.0]), "z": np.array([0.0, 0.0, 1.0])}
AXIS_COLORS = {"x": (220, 30, 30), "y": (30, 160, 30), "z": (40, 70, 230)}
AXIS_NAMES = {"x": "On Red Axis", "y": "On Green Axis", "z": "On Blue Axis"}

KIND_COLORS: dict[str, tuple[int, int, int]] = {
    "endpoint": (30, 160, 30),
    "midpoint": (0, 170, 200),
    "origin": (220, 140, 0),
    "on_edge": (220, 30, 30),
    "on_face": (40, 70, 230),
    "parallel": (200, 0, 200),
    "perpendicular": (200, 0, 200),
    "plane": (90, 90, 90),
}
LABELS = {
    "endpoint": "Endpoint",
    "midpoint": "Midpoint",
    "origin": "Origin",
    "on_edge": "On Edge",
    "on_face": "On Face",
    "parallel": "Parallel to Edge",
    "perpendicular": "Perpendicular to Edge",
    "plane": "",
}

AXIS_TOLERANCE = 10.0
"""Pixels from an axis line within which the cursor locks to it."""
ANGLE_TOLERANCE = 3.0
"""Degrees within which a direction counts as parallel or perpendicular."""


@dataclass
class Inference:
    """The snapped point under the cursor and why."""

    point: np.ndarray
    kind: str
    label: str = ""
    color: tuple[int, int, int] = (90, 90, 90)
    hit: Hit | None = None
    axis: str | None = None
    """``"x"``, ``"y"`` or ``"z"`` when the point lies on an axis line from the start point."""
    guide: tuple[np.ndarray, np.ndarray] | None = None
    """A guide line to draw (start, end) for axis/parallel inferences."""
    plane: Plane | None = None
    """Drawing plane suggested by the hit (a face's plane) or the fallback plane."""
    face: Face | None = None
    extra: dict = field(default_factory=dict)


class InferenceEngine:
    """Computes inferences against a :class:`PickScene`."""

    def __init__(self, scene: PickScene) -> None:
        self.scene = scene
        self.reference_edge: tuple[np.ndarray, np.ndarray] | None = None
        """Direction source for parallel/perpendicular inference (last edge hovered)."""

    def infer(
        self,
        camera: Camera,
        x: float,
        y: float,
        width: int,
        height: int,
        start: np.ndarray | None = None,
        lock: np.ndarray | None = None,
        plane: Plane | None = None,
    ) -> Inference:
        """Best inference for the cursor at ``(x, y)``.

        Args:
            start: The previous point while drawing (enables axis inference).
            lock: A locked direction through ``start`` (arrow keys or Shift).
            plane: The drawing plane to fall back on (default: the ground, or the
                horizontal plane through ``start``).
        """
        origin, direction = pixel_ray(camera, x, y, width, height)
        if start is not None and lock is not None:
            point = closest_point_on_line_to_ray(start, lock, origin, direction)
            axis = _axis_name(lock)
            color = AXIS_COLORS.get(axis or "", KIND_COLORS["parallel"])
            label = AXIS_NAMES.get(axis or "", "Locked")
            return Inference(point, "locked", label, color, axis=axis, guide=(start, point))
        faces = self.scene.ray_faces(origin, direction)
        front_depth = faces[0].depth if faces else math.inf
        for hit in self.scene.near_points(camera, x, y, width, height):
            if hit.depth <= front_depth * 1.001 + 1.0:
                kind = "endpoint" if hit.kind == "vertex" else "midpoint"
                if isinstance(hit.entity, Edge):
                    self.reference_edge = (hit.entity.v1.position, hit.entity.v2.position)
                return Inference(hit.point, kind, LABELS[kind], KIND_COLORS[kind], hit=hit)
        origin_hit = self._origin(camera, x, y, width, height)
        if origin_hit is not None:
            return origin_hit
        if start is not None:
            axis = self._axis(camera, x, y, width, height, start, origin, direction)
            if axis is not None:
                return axis
        edges = [h for h in self.scene.near_edges(camera, x, y, width, height) if h.depth <= front_depth * 1.001 + 1.0]
        draw_plane = plane or (Plane.from_point_normal(start, (0, 0, 1)) if start is not None else Plane.from_point_normal((0, 0, 0), (0, 0, 1)))
        if start is not None:
            para = self._parallel(start, origin, direction, faces, draw_plane)
            if para is not None:
                return para
        if edges:
            hit = edges[0]
            assert isinstance(hit.entity, Edge)
            world = hit.world
            a = world[:3, :3] @ hit.entity.v1.position + world[:3, 3]
            b = world[:3, :3] @ hit.entity.v2.position + world[:3, 3]
            self.reference_edge = (a, b)
            return Inference(hit.point, "on_edge", LABELS["on_edge"], KIND_COLORS["on_edge"], hit=hit)
        if faces:
            hit = faces[0]
            assert isinstance(hit.entity, Face)
            normal = self.scene.face_normals.get(id(hit.entity), hit.entity.normal)
            face_plane = Plane.from_point_normal(hit.point, normal)
            return Inference(hit.point, "on_face", LABELS["on_face"], KIND_COLORS["on_face"], hit=hit,
                             plane=face_plane, face=hit.entity)
        point = _ray_plane(origin, direction, draw_plane)
        if point is None:
            point = _ray_plane(origin, direction, Plane.from_point_normal(start if start is not None else np.zeros(3), -camera.basis()[2]))
        return Inference(point if point is not None else origin.copy(), "plane", "", KIND_COLORS["plane"], plane=draw_plane)

    # ------------------------------------------------------------------ helpers
    def _origin(self, camera: Camera, x: float, y: float, width: int, height: int) -> Inference | None:
        proj = self.scene.project(camera, np.zeros((1, 3)), width, height)
        if proj.visible[0] and float(np.linalg.norm(proj.xy[0] - [x, y])) <= 10.0:
            return Inference(np.zeros(3), "origin", LABELS["origin"], KIND_COLORS["origin"])
        return None

    def _axis(
        self, camera: Camera, x: float, y: float, width: int, height: int,
        start: np.ndarray, origin: np.ndarray, direction: np.ndarray,
    ) -> Inference | None:
        """Lock to the red/green/blue line through ``start`` the cursor is closest to."""
        best: tuple[float, str, np.ndarray] | None = None
        for name, axis in AXES.items():
            point = closest_point_on_line_to_ray(start, axis, origin, direction)
            if float(np.linalg.norm(point - start)) < 1e-6:
                continue
            proj = self.scene.project(camera, np.array([point]), width, height)
            if not proj.visible[0]:
                continue
            dist = float(np.linalg.norm(proj.xy[0] - [x, y]))
            if dist <= AXIS_TOLERANCE and (best is None or dist < best[0]):
                best = (dist, name, point)
        if best is None:
            return None
        _, name, point = best
        return Inference(point, f"axis_{name}", AXIS_NAMES[name], AXIS_COLORS[name], axis=name, guide=(start, point))

    def _parallel(
        self, start: np.ndarray, origin: np.ndarray, direction: np.ndarray, faces: list[Hit], plane: Plane
    ) -> Inference | None:
        """Parallel or perpendicular to the reference edge, within the drawing plane."""
        if self.reference_edge is None:
            return None
        a, b = self.reference_edge
        edge_dir = b - a
        if np.linalg.norm(edge_dir) < 1e-9:
            return None
        edge_dir = normalize(edge_dir)
        target = faces[0].point if faces else _ray_plane(origin, direction, plane)
        if target is None or np.linalg.norm(target - start) < 1e-6:
            return None
        want = normalize(target - start)
        candidates = [("parallel", edge_dir)]
        perp = np.cross(plane.normal, edge_dir)
        if np.linalg.norm(perp) > 1e-9:
            candidates.append(("perpendicular", normalize(perp)))
        for kind, d in candidates:
            if _axis_name(d) is not None:
                continue  # axis inference already covers axis-aligned directions
            angle = math.degrees(math.acos(min(1.0, abs(float(want @ d)))))
            if angle <= ANGLE_TOLERANCE:
                point = closest_point_on_line_to_ray(start, d, origin, direction)
                return Inference(point, kind, LABELS[kind], KIND_COLORS[kind], guide=(start, point))
        return None


def _axis_name(direction: np.ndarray) -> str | None:
    d = normalize(direction)
    for name, axis in AXES.items():
        if abs(float(d @ axis)) > 0.9999:
            return name
    return None


def _ray_plane(origin: np.ndarray, direction: np.ndarray, plane: Plane) -> np.ndarray | None:
    """Intersection of a ray with a plane (None if parallel or behind)."""
    denom = float(plane.normal @ direction)
    if abs(denom) < 1e-9:
        return None
    t = -plane.distance(origin) / denom
    if t < 0:
        return None
    return origin + direction * t
