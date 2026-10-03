"""Extrusion operations: push/pull, follow me, offset and extrude."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from pymodeler.core.entities import Edge, Entities, Face, Vertex
from pymodeler.core.planar import interior_point, signed_area
from pymodeler.core.sticky import FaceSpec, mergeable
from pymodeler.core.vec import (
    TOL,
    GeometryError,
    Plane,
    angle_between,
    newell_normal,
    norm,
    normalize,
    same_point,
    v3,
)


@dataclass
class PushPullResult:
    """Outcome of :func:`push_pull`."""

    caps: list[Face] = field(default_factory=list)
    """Faces at the pushed position (empty when the push cut all the way through)."""
    sides: list[Face] = field(default_factory=list)
    """Side faces created along the face's boundary."""

    @property
    def faces(self) -> list[Face]:
        """All faces the operation created that still exist."""
        return [f for f in self.caps + self.sides if f.alive]


def push_pull(
    entities: Entities, face: Face, distance: float, *, create_new: bool = False
) -> PushPullResult:
    """Extrude ``face`` along its normal by ``distance`` millimetres (SketchUp rules).

    * A *free* face (no edge shared with another face) becomes a closed solid; the
      original face is kept, flipped, as its back.  Negative distances extrude behind it.
    * A face on a solid moves: positive distances add material, negative ones cut a
      pocket.  New side faces coplanar with existing faces merge into them, and a cap
      or side landing on an opposite-facing face cancels both, which cuts through
      (door and window openings) or shortens the solid.
    * ``create_new`` keeps the original face in place (SketchUp's Ctrl modifier).

    Raises:
        GeometryError: if ``face`` does not belong to ``entities``.
    """
    if face.parent is not entities:
        raise GeometryError("the face does not belong to this collection")
    result = PushPullResult()
    if abs(distance) <= TOL:
        return result
    edges_with_faces = {e for e in entities.edges.values() if e.faces}
    free = all(len(e.faces) == 1 for e in face.edges())
    if free and distance < 0:
        face.reverse()
        distance = -distance
    normal = face.normal.copy()
    offset = normal * distance
    loops = [[v.position.copy() for v in loop] for loop in face.loops]
    loop_curves = _loop_curves(entities, face)
    material, back_material, tag = face.material, face.back_material, face.tag
    original_edges = face.edges()
    if free:
        face.reverse()
    elif not create_new:
        entities.erase_faces([face])

    specs: list[FaceSpec] = [
        FaceSpec(
            outer=[p + offset for p in loops[0]],
            holes=[[p + offset for p in hole] for hole in loops[1:]],
            normal=normal,
            material=material,
            back_material=back_material,
            tag=tag,
            cancels=True,
        )
    ]
    for loop in loops:
        for i, a in enumerate(loop):
            b = loop[(i + 1) % len(loop)]
            specs.append(
                FaceSpec(
                    outer=[a, b, b + offset, a + offset],
                    material=material,
                    back_material=back_material,
                    tag=tag,
                    cancels=True,
                )
            )
    log = entities.registry.begin()
    try:
        made = entities.add_faces(specs, cancel_opposite=True)
        result.caps = made[0]
        result.sides = [f for faces in made[1:] for f in faces]
        _mark_curves(entities, loops, loop_curves, offset)
        new_edges = [e for e in (entities.registry.get(i) for i in log.created) if isinstance(e, Edge)]
        candidates = original_edges + new_edges + [e for f in result.faces for e in f.edges()]
        entities.merge_coplanar(candidates)
        strays = [e for e in edges_with_faces | set(new_edges) if e.alive]
        entities.remove_stray_edges(strays)
    finally:
        entities.registry.end(log)
    result.caps = [f for f in result.caps if f.alive]
    result.sides = [f for f in result.sides if f.alive]
    return result


def _loop_curves(entities: Entities, face: Face) -> list[list[int | None]]:
    """Curve id of each boundary edge, per loop, in loop order."""
    out: list[list[int | None]] = []
    for loop in face.loops:
        ids: list[int | None] = []
        for i, v in enumerate(loop):
            e = entities.edge_between(v, loop[(i + 1) % len(loop)])
            ids.append(e.curve if e is not None else None)
        out.append(ids)
    return out


def _mark_curves(
    entities: Entities,
    loops: list[list[np.ndarray]],
    curves: list[list[int | None]],
    offset: np.ndarray,
) -> None:
    """Copy curves to the cap and soften side edges that sweep along a curve."""
    remap: dict[int, int] = {}
    for loop, ids in zip(loops, curves, strict=True):
        count = len(loop)
        for i in range(count):
            curve = ids[i]
            if curve is not None:
                a = entities.find_vertex(loop[i] + offset)
                b = entities.find_vertex(loop[(i + 1) % count] + offset)
                cap_edge = entities.edge_between(a, b) if a and b else None
                if cap_edge is not None:
                    cap_edge.curve = remap.setdefault(curve, entities.registry.new_id())
            if curve is not None and ids[i - 1] == curve:
                lo = entities.find_vertex(loop[i])
                hi = entities.find_vertex(loop[i] + offset)
                side = entities.edge_between(lo, hi) if lo and hi else None
                if side is not None:
                    side.soft = side.smooth = True


# ---------------------------------------------------------------------- follow me

SMOOTH_ANGLE = 25.0
"""Path turns below this many degrees produce soft, smooth ring edges."""


def follow_me(
    entities: Entities,
    face: Face,
    path: Sequence[np.ndarray],
    closed: bool | None = None,
) -> list[Face]:
    """Sweep ``face`` along a polyline ``path`` (SketchUp's Follow Me).

    The profile is carried along each path segment and mitred at every path vertex.
    An open path gets end caps; a closed path (first point == last point, or
    ``closed=True``) produces a ring, e.g. a lathe when the path circles an axis.

    Returns:
        The faces created.

    Raises:
        GeometryError: on a path with fewer than two points or one that doubles back.
    """
    if face.parent is not entities:
        raise GeometryError("the face does not belong to this collection")
    pts = _dedupe_path([v3(p) for p in path])
    if closed is None:
        closed = len(pts) > 3 and same_point(pts[0], pts[-1])
    if closed and same_point(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 2:
        raise GeometryError("follow me needs a path with at least two points")
    count = len(pts)
    seg_count = count if closed else count - 1
    dirs = [normalize(pts[(k + 1) % count] - pts[k]) for k in range(seg_count)]
    miters = [_miter(dirs, k, closed, count) for k in range(count)]
    loops = [[v.position.copy() for v in loop] for loop in face.loops]
    if float(face.normal @ dirs[0]) < 0:
        loops = [list(reversed(loop)) for loop in loops]
    curves = _profile_curve_flags(entities, face, reverse=float(face.normal @ dirs[0]) < 0)
    material, back_material, tag = face.material, face.back_material, face.tag

    rings = [[[_project(p, dirs[0], pts[0], miters[0]) for p in loop] for loop in loops]]
    for k in range(seg_count):
        nxt = (k + 1) % count
        rings.append([[_project(p, dirs[k], pts[nxt], miters[nxt]) for p in loop] for loop in rings[-1]])
    if closed:
        rings[-1] = rings[0]
    on_start = all(
        same_point(p, q) for loop, ring in zip(loops, rings[0], strict=True) for p, q in zip(loop, ring, strict=True)
    )
    profile_edges = face.edges()
    if on_start or closed:
        # The profile becomes part of the sweep (or, for a ring, would be left inside it).
        entities.erase_faces([face])

    specs: list[FaceSpec] = []
    for k in range(seg_count):
        for loop_a, loop_b in zip(rings[k], rings[k + 1], strict=True):
            n = len(loop_a)
            for i in range(n):
                quad = _quad(loop_a[i], loop_a[(i + 1) % n], loop_b[(i + 1) % n], loop_b[i])
                if quad is not None:
                    specs.append(FaceSpec(quad, material=material, back_material=back_material, tag=tag))
    if not closed:
        start = [list(reversed(loop)) for loop in rings[0]]
        specs.append(FaceSpec(start[0], start[1:], normal=-dirs[0], material=material,
                              back_material=back_material, tag=tag))
        end = rings[-1]
        specs.append(FaceSpec(end[0], end[1:], normal=dirs[-1], material=material,
                              back_material=back_material, tag=tag))
    made = entities.add_faces(specs)
    if closed and not on_start:
        # The profile's outline is left on the ring's faces: drop it where it is not needed.
        leftovers = [e for e in profile_edges if e.alive and len(e.faces) != 1]
        seams = [e for e in leftovers if len(e.faces) == 2 and mergeable(*e.faces)]
        strays = [e for e in leftovers if not e.faces]
        if seams:
            entities.erase_edges(seams, heal=True)
        if strays:
            entities.erase_edges(strays, heal=False)
    _soften_sweep(entities, rings, curves, dirs, closed)
    return [f for faces in made for f in faces if f.alive]


def _dedupe_path(pts: list[np.ndarray]) -> list[np.ndarray]:
    """Drop consecutive duplicate path points."""
    out: list[np.ndarray] = []
    for p in pts:
        if not out or not same_point(out[-1], p):
            out.append(p)
    return out


def _miter(dirs: list[np.ndarray], k: int, closed: bool, count: int) -> np.ndarray:
    """Normal of the mitre plane at path vertex ``k``."""
    if not closed and k == 0:
        return dirs[0]
    if not closed and k == count - 1:
        return dirs[-1]
    m = dirs[k - 1] + dirs[k % len(dirs)]
    if norm(m) < 1e-9:
        raise GeometryError("the path doubles back on itself")
    return normalize(m)


def _project(p: np.ndarray, d: np.ndarray, origin: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Slide ``p`` along ``d`` until it reaches the plane (``origin``, ``normal``)."""
    denom = float(d @ normal)
    if abs(denom) < 1e-12:
        raise GeometryError("the path turns too sharply for the profile")
    t = float((origin - p) @ normal) / denom
    return p + d * t


def _quad(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> list[np.ndarray] | None:
    """A side face ``a b c d`` with collapsed corners removed (None if degenerate)."""
    pts: list[np.ndarray] = []
    for p in (a, b, c, d):
        if not pts or not same_point(pts[-1], p):
            pts.append(p)
    if len(pts) > 1 and same_point(pts[0], pts[-1]):
        pts.pop()
    if len(pts) < 3 or norm(newell_normal(pts)) < 1e-9:
        return None
    return pts


def _profile_curve_flags(entities: Entities, face: Face, reverse: bool) -> list[list[bool]]:
    """Per loop and vertex: True if the profile is smooth there (both adjacent edges
    belong to one curve, or the outline turns by less than :data:`SMOOTH_ANGLE`)."""
    flags: list[list[bool]] = []
    for loop in face.loops:
        verts = list(reversed(loop)) if reverse else loop
        n = len(verts)
        curves = []
        for i in range(n):
            e = entities.edge_between(verts[i], verts[(i + 1) % n])
            curves.append(e.curve if e is not None else None)
        smooth = []
        for i in range(n):
            same_curve = curves[i] is not None and curves[i] == curves[i - 1]
            turn = angle_between(verts[i].position - verts[i - 1].position,
                                 verts[(i + 1) % n].position - verts[i].position)
            smooth.append(same_curve or turn < SMOOTH_ANGLE)
        flags.append(smooth)
    return flags


def _soften_sweep(
    entities: Entities,
    rings: list[list[list[np.ndarray]]],
    curves: list[list[bool]],
    dirs: list[np.ndarray],
    closed: bool,
) -> None:
    """Soften edges along curved profiles and around smoothly turning path vertices."""
    for k in range(len(rings) - 1):
        for li, loop in enumerate(rings[k]):
            for i, p in enumerate(loop):
                if curves[li][i]:
                    for e in entities.edges_on_segment(p, rings[k + 1][li][i]):
                        e.soft = e.smooth = True
    inner = range(len(rings) - 1) if closed else range(1, len(rings) - 1)
    for k in inner:
        turn = angle_between(dirs[k - 1], dirs[k % len(dirs)])
        if turn >= SMOOTH_ANGLE:
            continue
        for loop in rings[k]:
            for i, p in enumerate(loop):
                for e in entities.edges_on_segment(p, loop[(i + 1) % len(loop)]):
                    e.soft = e.smooth = True


def edge_chain(edge: Edge, exclude: Iterable[Edge] = ()) -> list[Edge]:
    """The run of edges through ``edge`` that continues while vertices join exactly two edges.

    Edges in ``exclude`` (e.g. the profile's own edges) are ignored.  A closed loop of
    edges is returned whole.
    """
    skip = set(exclude)
    chain: dict[Edge, None] = {edge: None}
    for start in (edge.v1, edge.v2):
        current, previous = start, edge
        while True:
            others = [e for e in current.edges if e not in skip and e is not previous]
            if len(others) != 1 or others[0] in chain:
                break
            previous = others[0]
            chain[previous] = None
            current = previous.other(current)
    return list(chain)


def start_path_near(points: Sequence[np.ndarray], face: Face) -> list[np.ndarray]:
    """Reorder a path so it starts at the point nearest the profile ``face``.

    An open path is reversed if its far end is the nearer one; a closed path (first
    point == last point) is rotated to start at its nearest vertex.
    """
    pts = [v3(p) for p in points]
    if len(pts) < 2:
        return pts

    def distance(p: np.ndarray) -> float:
        plane = face.plane
        off = plane.distance(p)
        projected = p - plane.normal * off
        if face.contains_point(projected, tol=1.0):
            return abs(off)
        return abs(off) + min(float(np.linalg.norm(projected - v.position)) for v in face.outer_loop)

    if len(pts) > 3 and same_point(pts[0], pts[-1]):
        ring = pts[:-1]
        k = min(range(len(ring)), key=lambda i: distance(ring[i]))
        ring = ring[k:] + ring[:k]
        # Travel the way that leaves the profile most nearly perpendicular to the path.
        forward = abs(float(face.normal @ normalize(ring[1] - ring[0])))
        backward = abs(float(face.normal @ normalize(ring[-1] - ring[0])))
        if backward > forward + 1e-9:
            ring = [ring[0]] + ring[1:][::-1]
        return ring + [ring[0].copy()]
    return pts[::-1] if distance(pts[-1]) < distance(pts[0]) - TOL else pts


def path_from_edges(edges: Sequence[Edge]) -> list[np.ndarray]:
    """Order a connected chain of edges into a list of points.

    Raises:
        GeometryError: if the edges do not form a single chain.
    """
    if not edges:
        raise GeometryError("the path has no edges")
    adjacency: dict[Vertex, list[Edge]] = {}
    for e in edges:
        for v in e.vertices:
            adjacency.setdefault(v, []).append(e)
    if any(len(es) > 2 for es in adjacency.values()):
        raise GeometryError("the path branches; follow me needs a single chain of edges")
    ends = [v for v, es in adjacency.items() if len(es) == 1]
    start = ends[0] if ends else edges[0].v1
    pts = [start.position.copy()]
    used: set[Edge] = set()
    current = start
    while True:
        nxt = next((e for e in adjacency[current] if e not in used), None)
        if nxt is None:
            break
        used.add(nxt)
        current = nxt.other(current)
        pts.append(current.position.copy())
    if len(used) != len(set(edges)):
        raise GeometryError("the path edges are not connected")
    return pts


# ---------------------------------------------------------------------- offset & extrude


def offset_face(entities: Entities, face: Face, distance: float) -> list[Face]:
    """Draw a copy of the face's boundary ``distance`` mm inside it (negative: outside).

    Inward offsets apply to the outer loop and holes; outward offsets to the outer
    loop only.  The new loop splits the face (sticky geometry).

    Returns:
        The face(s) enclosed by the new outer loop.

    Raises:
        GeometryError: if the distance is so large that the outline would invert.
    """
    if face.parent is not entities:
        raise GeometryError("the face does not belong to this collection")
    if abs(distance) <= TOL:
        raise GeometryError("offset distance must be non-zero")
    plane = face.plane
    loops2d = face.loops_2d(plane)
    new_outer = _offset_polygon(loops2d[0], distance)
    new_holes = [_offset_polygon(h, distance) for h in loops2d[1:]] if distance > 0 else []
    segments = []
    for loop in [new_outer, *new_holes]:
        pts3 = [plane.to_3d(p) for p in loop]
        segments.extend((pts3[i], pts3[(i + 1) % len(pts3)]) for i in range(len(pts3)))
    entities.add_edges(segments)
    probe = plane.to_3d(interior_point(new_outer, new_holes))
    return [f for f in entities.faces_in_plane(plane) if f.contains_point(probe)]


def _offset_polygon(loop: list[tuple[float, float]], distance: float) -> list[tuple[float, float]]:
    """Offset every edge of a 2D loop to its left by ``distance`` (mitred corners)."""
    n = len(loop)
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = loop[i], loop[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        nx, ny = -dy / length, dx / length
        lines.append(((x1 + nx * distance, y1 + ny * distance), (dx / length, dy / length)))
    out: list[tuple[float, float]] = []
    m = len(lines)
    for i in range(m):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < 1e-12:
            out.append(p2)
            continue
        t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / det
        out.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t))
    if len(out) < 3 or (signed_area(out) > 0) != (signed_area(loop) > 0):
        raise GeometryError("offset distance is too large for this face")
    for i in range(len(out)):
        a, b = out[i], out[(i + 1) % len(out)]
        (oa, ob) = loop[i % n], loop[(i + 1) % n]
        if (b[0] - a[0]) * (ob[0] - oa[0]) + (b[1] - a[1]) * (ob[1] - oa[1]) < 0:
            raise GeometryError("offset distance is too large for this face")
    return out


def extrude(
    entities: Entities, points: Sequence[np.ndarray], direction: np.ndarray
) -> list[Face]:
    """Make a prism: the face outlined by ``points``, swept straight along ``direction``.

    ``direction`` may be oblique to the profile (a sheared prism, e.g. a sloped rail).

    Returns:
        All faces of the resulting solid.

    Raises:
        GeometryError: if the direction lies in the profile's plane.
    """
    length = norm(direction)
    if length <= TOL:
        raise GeometryError("extrude direction must be non-zero")
    pts = [v3(p) for p in points]
    plane = Plane.from_points(pts)
    along = float(plane.normal @ direction)
    if abs(along) < 1e-9 * length:
        raise GeometryError("extrude direction lies in the profile's plane")
    if along < 0:
        pts = list(reversed(pts))  # make the outline counter-clockwise around the direction
    n = plane.normal if along > 0 else -plane.normal
    top = [p + direction for p in pts]
    specs = [FaceSpec(pts, normal=-n), FaceSpec(top, normal=n)]
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        specs.append(FaceSpec([a, b, b + direction, a + direction]))
    made = entities.add_faces(specs)
    return list(dict.fromkeys(f for faces in made for f in faces if f.alive))
