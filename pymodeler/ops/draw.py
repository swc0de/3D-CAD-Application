"""Drawing operations: lines, rectangles, circles, arcs, polygons and faces.

Every function draws into an :class:`~pymodeler.core.entities.Entities` collection with
full sticky behaviour and returns a :class:`Drawn` with the faces and edges it produced.
Points are in the collection's coordinates (millimetres).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from pymodeler.core.entities import Edge, Entities, Face
from pymodeler.core.vec import GeometryError, PointLike, any_perpendicular, normalize, v3

PlaneAxes = tuple[np.ndarray, np.ndarray, np.ndarray]
"""Drawing axes ``(u, v, normal)``."""

PLANE_AXES: dict[str, tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]] = {
    "xy": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "xz": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "yz": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}
"""Named drawing planes as ``(u, v, normal)``; the normals face up, front and right."""


@dataclass
class Drawn:
    """Geometry produced by a drawing operation."""

    faces: list[Face] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @property
    def entities(self) -> list[Face | Edge]:
        """Faces followed by edges, skipping anything erased since."""
        return [f for f in self.faces if f.alive] + [e for e in self.edges if e.alive]


def plane_axes(
    plane: str | None = "xy",
    normal: PointLike | None = None,
    x_axis: PointLike | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resolve drawing axes ``(u, v, n)`` from a plane name or an explicit normal.

    ``u x v == n`` always holds.  With an explicit ``normal`` the ``x_axis`` (projected
    into the plane) fixes the in-plane rotation; otherwise a natural axis is chosen.

    Raises:
        GeometryError: on an unknown plane name or a degenerate axis.
    """
    if normal is not None:
        n = normalize(v3(normal))
        if x_axis is not None:
            x = v3(x_axis)
            u = normalize(x - n * float(x @ n))
        else:
            u = _natural_u(n)
        return u, np.cross(n, u), n
    name = (plane or "xy").lower()
    if name not in PLANE_AXES:
        raise GeometryError(f"unknown plane {plane!r} (use xy, xz or yz)")
    u, v, n = (np.array(a, dtype=float) for a in PLANE_AXES[name])
    if x_axis is not None:
        x = v3(x_axis)
        u = normalize(x - n * float(x @ n))
        v = np.cross(n, u)
    return u, v, n


def _natural_u(n: np.ndarray) -> np.ndarray:
    """In-plane X axis matching the named planes for axis-aligned normals."""
    for name in ("xy", "xz", "yz"):
        u, _, pn = PLANE_AXES[name]
        if abs(abs(float(np.dot(pn, n))) - 1.0) < 1e-9:
            return np.array(u, dtype=float)
    return any_perpendicular(n)


def line(entities: Entities, points: Sequence[PointLike], closed: bool = False) -> Drawn:
    """Draw connected line segments (a closed coplanar loop becomes a face).

    Raises:
        GeometryError: with fewer than two distinct points.
    """
    pts = [v3(p) for p in points]
    if len(pts) < 2:
        raise GeometryError("a line needs at least two points")
    with entities.registry.tracking() as log:
        edges = entities.add_polyline(pts, closed=closed)
    if not edges:
        raise GeometryError("line points are all the same")
    return Drawn(faces=_created_faces(entities, log.created), edges=edges)


def face(
    entities: Entities,
    points: Sequence[PointLike],
    holes: Sequence[Sequence[PointLike]] = (),
    normal: PointLike | None = None,
    material: str | None = None,
) -> Drawn:
    """Draw a face from an explicit outline (plus optional hole outlines)."""
    faces = entities.add_face(points, holes, normal=normal, material=material)
    return Drawn(faces=faces, edges=_edges_of(faces))


def rectangle(
    entities: Entities,
    origin: PointLike,
    width: float,
    depth: float,
    axes: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    centered: bool = False,
) -> Drawn:
    """Draw a ``width`` x ``depth`` rectangle with a corner (or centre) at ``origin``.

    ``axes`` is ``(u, v, n)`` from :func:`plane_axes` (default: the ground plane).
    The face's front points along ``n``.

    Raises:
        GeometryError: if either side is zero.
    """
    if abs(width) < 1e-9 or abs(depth) < 1e-9:
        raise GeometryError("rectangle width and depth must be non-zero")
    u, v, n = axes if axes is not None else plane_axes("xy")
    o = v3(origin)
    if centered:
        o = o - (u * width + v * depth) * 0.5
    corners = [o, o + u * width, o + u * width + v * depth, o + v * depth]
    faces = entities.add_face(corners, normal=n)
    return Drawn(faces=faces, edges=_edges_of(faces))


def circle(
    entities: Entities,
    center: PointLike,
    radius: float,
    segments: int = 24,
    axes: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> Drawn:
    """Draw a circular face approximated by ``segments`` edges (marked as one curve).

    Raises:
        GeometryError: on a non-positive radius or fewer than 3 segments.
    """
    if radius <= 0:
        raise GeometryError("circle radius must be positive")
    pts = _ring(center, radius, segments, axes, 0.0, 2 * math.pi, closed=True)
    n = (axes if axes is not None else plane_axes("xy"))[2]
    faces = entities.add_face(pts, normal=n)
    edges = _mark_curve(entities, pts, closed=True)
    return Drawn(faces=faces, edges=edges)


def polygon(
    entities: Entities,
    center: PointLike,
    radius: float,
    sides: int,
    axes: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    inscribed: bool = True,
) -> Drawn:
    """Draw a regular polygon face.

    ``inscribed`` places the vertices on the radius; otherwise the edge midpoints are.

    Raises:
        GeometryError: on a non-positive radius or fewer than 3 sides.
    """
    if radius <= 0:
        raise GeometryError("polygon radius must be positive")
    r = radius if inscribed else radius / math.cos(math.pi / max(sides, 3))
    pts = _ring(center, r, sides, axes, 0.0, 2 * math.pi, closed=True)
    n = (axes if axes is not None else plane_axes("xy"))[2]
    faces = entities.add_face(pts, normal=n)
    return Drawn(faces=faces, edges=_edges_of(faces))


def arc(
    entities: Entities,
    center: PointLike,
    radius: float,
    start_angle: float,
    end_angle: float,
    segments: int = 12,
    axes: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    close: str = "none",
) -> Drawn:
    """Draw an arc (angles in degrees, counter-clockwise from the plane's U axis).

    Args:
        close: ``"none"`` for an open curve, ``"chord"`` to close it with a straight
            line, or ``"pie"`` to close it through the centre (both make a face).

    Raises:
        GeometryError: on a non-positive radius, zero sweep or bad ``close`` value.
    """
    if radius <= 0:
        raise GeometryError("arc radius must be positive")
    sweep = math.radians(end_angle - start_angle)
    if abs(sweep) < 1e-9:
        raise GeometryError("arc start and end angles must differ")
    if close not in ("none", "chord", "pie"):
        raise GeometryError("arc 'close' must be none, chord or pie")
    pts = _ring(center, radius, segments, axes, math.radians(start_angle), sweep, closed=False)
    with entities.registry.tracking() as log:
        entities.add_polyline(pts)
        edges = _mark_curve(entities, pts, closed=False)
        if close == "chord":
            entities.add_line(pts[-1], pts[0])
        elif close == "pie":
            entities.add_polyline([pts[-1], v3(center), pts[0]])
    return Drawn(faces=_created_faces(entities, log.created), edges=edges)


def _ring(
    center: PointLike,
    radius: float,
    segments: int,
    axes: tuple[np.ndarray, np.ndarray, np.ndarray] | None,
    start: float,
    sweep: float,
    closed: bool,
) -> list[np.ndarray]:
    """Points around a circle or along an arc."""
    if segments < (3 if closed else 1):
        raise GeometryError("not enough segments")
    u, v, _ = axes if axes is not None else plane_axes("xy")
    c = v3(center)
    count = segments if closed else segments + 1
    pts = []
    for k in range(count):
        t = start + sweep * k / segments
        pts.append(c + radius * (math.cos(t) * u + math.sin(t) * v))
    return pts


def _mark_curve(entities: Entities, pts: list[np.ndarray], closed: bool) -> list[Edge]:
    """Tag the edges along ``pts`` with one new curve id; returns those edges."""
    curve = entities.registry.new_id()
    out: list[Edge] = []
    count = len(pts) if closed else len(pts) - 1
    for i in range(count):
        for e in entities.edges_on_segment(pts[i], pts[(i + 1) % len(pts)]):
            e.curve = curve
            out.append(e)
    return out


def _created_faces(entities: Entities, created: dict[int, None]) -> list[Face]:
    """Faces of ``entities`` whose ids appear in a change log's created set."""
    return [f for f in entities.faces.values() if f.id in created]


def _edges_of(faces: Sequence[Face]) -> list[Edge]:
    """Distinct edges bounding the given faces."""
    out: dict[Edge, None] = {}
    for f in faces:
        for e in f.edges():
            out[e] = None
    return list(out)
