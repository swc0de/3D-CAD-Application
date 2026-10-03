"""Extrusion operations: push/pull."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.entities import Edge, Entities, Face
from pymodeler.core.sticky import FaceSpec
from pymodeler.core.vec import TOL, GeometryError


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
