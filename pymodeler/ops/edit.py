"""Erase and Intersect Faces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from pymodeler.core.entities import Edge, Entities, Entity, Face
from pymodeler.core.transform import apply_point, apply_points, inverse
from pymodeler.core.vec import TOL, Plane, is_parallel, normalize


def erase(entities: Entities, targets: Sequence[Entity]) -> None:
    """Erase faces, edges, vertices and instances (SketchUp eraser rules for edges)."""
    entities.erase([t for t in targets if t.parent is entities])


@dataclass
class WorldFace:
    """A face together with the transform from its collection to world space."""

    face: Face
    world: np.ndarray

    def loops(self) -> list[np.ndarray]:
        """Loops in world coordinates."""
        return [apply_points(self.world, pts) for pts in self.face.loop_points()]


def intersect_faces(items: Sequence[WorldFace], others: Sequence[WorldFace]) -> list[Edge]:
    """Add the intersection lines between two sets of faces (Intersect Faces).

    Each intersection segment is added to the collections of *both* faces involved,
    so the faces are split along it.  Coplanar overlaps are not handled.

    Returns:
        The edges created.
    """
    pending: dict[int, tuple[Entities, np.ndarray, list[tuple[np.ndarray, np.ndarray]]]] = {}
    for a in items:
        for b in others:
            if a.face is b.face:
                continue
            for p, q in face_face_segments(a, b):
                for wf in (a, b):
                    ents = wf.face.parent
                    if ents is None:
                        continue
                    entry = pending.setdefault(id(ents), (ents, inverse(wf.world), []))
                    entry[2].append((apply_point(entry[1], p), apply_point(entry[1], q)))
    created: list[Edge] = []
    for ents, _, segments in pending.values():
        with ents.registry.tracking() as log:
            ents.add_edges(segments, auto_faces=False)
        created.extend(e for e in ents.edges.values() if e.id in log.created)
    return created


def face_face_segments(a: WorldFace, b: WorldFace) -> list[tuple[np.ndarray, np.ndarray]]:
    """World-space segments where two (non-coplanar) faces cross each other."""
    la, lb = a.loops(), b.loops()
    try:
        pa, pb = Plane.from_points(list(la[0])), Plane.from_points(list(lb[0]))
    except ValueError:
        return []
    if is_parallel(pa.normal, pb.normal):
        return []
    direction = normalize(np.cross(pa.normal, pb.normal))
    origin = _line_point(pa, pb)
    spans_a = _clip_line(la, pa, origin, direction)
    spans_b = _clip_line(lb, pb, origin, direction)
    out = []
    for s0, s1 in spans_a:
        for t0, t1 in spans_b:
            lo, hi = max(s0, t0), min(s1, t1)
            if hi - lo > TOL:
                out.append((origin + direction * lo, origin + direction * hi))
    return out


def _line_point(p1: Plane, p2: Plane) -> np.ndarray:
    """A point on the intersection line of two non-parallel planes."""
    n1, n2 = p1.normal, p2.normal
    d = np.cross(n1, n2)
    return (np.cross(n2, d) * p1.offset + np.cross(d, n1) * p2.offset) / float(d @ d)


def _clip_line(
    loops: list[np.ndarray], plane: Plane, origin: np.ndarray, direction: np.ndarray
) -> list[tuple[float, float]]:
    """Parameter intervals of the line ``origin + t * direction`` inside a face."""
    side = np.cross(plane.normal, direction)
    ts: list[float] = []
    for loop in loops:
        n = len(loop)
        for i in range(n):
            p, q = loop[i], loop[(i + 1) % n]
            sp = float((p - origin) @ side)
            sq = float((q - origin) @ side)
            if (sp >= 0) == (sq >= 0):
                continue
            x = p + (q - p) * (sp / (sp - sq))
            ts.append(float((x - origin) @ direction))
    ts.sort()
    spans = [(ts[i], ts[i + 1]) for i in range(0, len(ts) - 1, 2)]
    return [(s, t) for s, t in spans if t - s > TOL]
