"""Primitive solids built from the basic operations: box, cylinder, cone, sphere."""

from __future__ import annotations

import math

import numpy as np

from pymodeler.core.entities import Entities, Face
from pymodeler.core.sticky import FaceSpec
from pymodeler.core.vec import GeometryError, PointLike, normalize, v3
from pymodeler.ops import draw
from pymodeler.ops.extrude import push_pull


def box(entities: Entities, origin: PointLike, size: PointLike, centered: bool = False) -> list[Face]:
    """An axis-aligned box from its minimum corner (or its base centre if ``centered``).

    Raises:
        GeometryError: if any dimension is zero.
    """
    sx, sy, sz = (float(c) for c in v3(size))
    if min(abs(sx), abs(sy), abs(sz)) < 1e-9:
        raise GeometryError("box dimensions must be non-zero")
    o = v3(origin)
    if centered:
        o = o - np.array([sx / 2, sy / 2, 0.0])
    lo = np.minimum(o, o + [sx, sy, sz])
    base = draw.rectangle(entities, lo, abs(sx), abs(sy))
    if len(base.faces) != 1:
        raise GeometryError("the box base overlaps existing geometry; build it inside a group")
    with entities.registry.tracking() as log:
        push_pull(entities, base.faces[0], abs(sz))
    return [f for f in entities.faces.values() if f.id in log.created or f is base.faces[0]]


def cylinder(
    entities: Entities,
    center: PointLike,
    radius: float,
    height: float,
    segments: int = 24,
    axis: PointLike = (0, 0, 1),
) -> list[Face]:
    """A cylinder standing on its base centre along ``axis``."""
    if height <= 0:
        raise GeometryError("cylinder height must be positive")
    axes = draw.plane_axes(normal=axis)
    base = draw.circle(entities, center, radius, segments, axes)
    if len(base.faces) != 1:
        raise GeometryError("the cylinder base overlaps existing geometry; build it inside a group")
    with entities.registry.tracking() as log:
        push_pull(entities, base.faces[0], height)
    return [f for f in entities.faces.values() if f.id in log.created or f is base.faces[0]]


def cone(
    entities: Entities,
    center: PointLike,
    radius: float,
    height: float,
    segments: int = 24,
    top_radius: float = 0.0,
    axis: PointLike = (0, 0, 1),
) -> list[Face]:
    """A cone (or a frustum when ``top_radius > 0``) standing on its base centre.

    With ``segments=4`` this makes a square pyramid.

    Raises:
        GeometryError: on non-positive radius/height or a negative top radius.
    """
    if radius <= 0 or height <= 0 or top_radius < 0:
        raise GeometryError("cone radius and height must be positive (top_radius >= 0)")
    u, v, n = draw.plane_axes(normal=axis)
    c = v3(center)
    ring = _ring(c, radius, segments, u, v)
    top_c = c + n * height
    specs = [FaceSpec(list(reversed(ring)), normal=-n)]
    if top_radius > 0:
        top = _ring(top_c, top_radius, segments, u, v)
        specs.append(FaceSpec(top, normal=n))
        for i in range(segments):
            j = (i + 1) % segments
            specs.append(FaceSpec([ring[i], ring[j], top[j], top[i]]))
    else:
        for i in range(segments):
            specs.append(FaceSpec([ring[i], ring[(i + 1) % segments], top_c]))
    made = entities.add_faces(specs)
    _soften_between(entities, [p for p in ring], top_c if top_radius <= 0 else None,
                    _ring(top_c, top_radius, segments, u, v) if top_radius > 0 else None, segments)
    return [f for faces in made for f in faces]


def sphere(
    entities: Entities, center: PointLike, radius: float, segments: int = 24, rings: int = 12
) -> list[Face]:
    """A UV sphere with ``segments`` around and ``rings`` from pole to pole.

    Raises:
        GeometryError: on a non-positive radius or too few segments/rings.
    """
    if radius <= 0 or segments < 3 or rings < 2:
        raise GeometryError("sphere needs radius > 0, segments >= 3 and rings >= 2")
    c = v3(center)
    grid = []
    for r in range(rings + 1):
        phi = math.pi * r / rings
        z = math.cos(phi)
        s = math.sin(phi)
        grid.append([c + radius * np.array([s * math.cos(2 * math.pi * k / segments),
                                            s * math.sin(2 * math.pi * k / segments), z])
                     for k in range(segments)])
    specs = []
    for r in range(rings):
        for k in range(segments):
            k2 = (k + 1) % segments
            if r == 0:
                pts = [grid[0][0], grid[1][k], grid[1][k2]]
            elif r == rings - 1:
                pts = [grid[r][k], grid[r + 1][0], grid[r][k2]]
            else:
                pts = [grid[r][k], grid[r + 1][k], grid[r + 1][k2], grid[r][k2]]
            specs.append(FaceSpec(pts))
    made = entities.add_faces(specs)
    faces = [f for fs in made for f in fs]
    for f in faces:
        for e in f.edges():
            e.soft = e.smooth = True
    return faces


def _ring(c: np.ndarray, r: float, n: int, u: np.ndarray, v: np.ndarray) -> list[np.ndarray]:
    """Points on a circle."""
    if n < 3:
        raise GeometryError("need at least 3 segments")
    return [c + r * (math.cos(2 * math.pi * k / n) * u + math.sin(2 * math.pi * k / n) * v) for k in range(n)]


def _soften_between(
    entities: Entities,
    ring: list[np.ndarray],
    apex: np.ndarray | None,
    top: list[np.ndarray] | None,
    segments: int,
) -> None:
    """Soften the slanted edges of a cone/frustum and mark the rims as curves."""
    if segments < 6:
        return  # pyramids keep hard edges
    for i, p in enumerate(ring):
        q = apex if apex is not None else top[i]  # type: ignore[index]
        for e in entities.edges_on_segment(p, q):
            e.soft = e.smooth = True
    for pts in (ring, top):
        if pts is None:
            continue
        curve = entities.registry.new_id()
        for i in range(segments):
            for e in entities.edges_on_segment(pts[i], pts[(i + 1) % segments]):
                e.curve = curve


def axis_vector(axis: PointLike | str) -> np.ndarray:
    """Turn ``"x"``/``"y"``/``"z"`` (optionally signed) or a vector into a unit vector."""
    if isinstance(axis, str):
        name = axis.strip().lower()
        sign = -1.0 if name.startswith("-") else 1.0
        name = name.lstrip("+-")
        table = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
        if name not in table:
            raise GeometryError(f"unknown axis {axis!r} (use x, y, z or a vector)")
        return np.array(table[name], dtype=float) * sign
    return normalize(v3(axis))
