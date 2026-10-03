"""Move, rotate, scale, copy and array operations, plus geometry copying helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from pymodeler.core.components import ComponentInstance, add_instance
from pymodeler.core.entities import Edge, Entities, Entity, Face, Vertex
from pymodeler.core.sticky import FaceSpec
from pymodeler.core.transform import apply_normal, apply_point, is_mirroring, translation


@dataclass
class Copied:
    """Entities produced by copying geometry."""

    faces: list[Face] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    instances: list[ComponentInstance] = field(default_factory=list)

    @property
    def entities(self) -> list[Entity]:
        """Everything created that still exists."""
        items: list[Entity] = [*self.faces, *self.edges, *self.instances]
        return [e for e in items if e.alive]


def split_targets(targets: Iterable[Entity]) -> tuple[list[Face], list[Edge], list[Vertex], list[ComponentInstance]]:
    """Sort entities into faces, edges, vertices and instances."""
    faces, edges, verts, insts = [], [], [], []
    for t in targets:
        if isinstance(t, Face):
            faces.append(t)
        elif isinstance(t, Edge):
            edges.append(t)
        elif isinstance(t, Vertex):
            verts.append(t)
        elif isinstance(t, ComponentInstance):
            insts.append(t)
    return faces, edges, verts, insts


def transform_entities(entities: Entities, targets: Sequence[Entity], matrix: np.ndarray) -> None:
    """Apply ``matrix`` to raw geometry (by moving its vertices) and to instances.

    Geometry connected to unselected geometry stretches, as in SketchUp.  Mirroring
    transforms flip the affected faces so their fronts stay outward.
    """
    faces, edges, verts, insts = split_targets(targets)
    moved: dict[Vertex, None] = dict.fromkeys(verts)
    for f in faces:
        moved.update(dict.fromkeys(f.vertices()))
    for e in edges:
        moved.update(dict.fromkeys(e.vertices))
    for v in moved:
        if v.parent is entities:
            v.position = apply_point(matrix, v.position)
    if is_mirroring(matrix):
        for f in entities.faces.values():
            if all(v in moved for v in f.outer_loop):
                f.reverse()
    for inst in insts:
        inst.transform = matrix @ inst.transform
        entities.registry.modified(inst.id)


def copy_geometry(
    source: Entities,
    targets: Sequence[Entity],
    dest: Entities,
    matrix: np.ndarray,
    inherit_material: str | None = None,
) -> Copied:
    """Copy raw geometry and instances from ``source`` into ``dest`` under ``matrix``.

    Copies stick to whatever already exists in ``dest``.  Edge styling (soft, smooth,
    hidden, curves) is preserved; each copied curve gets a fresh curve id.

    Args:
        inherit_material: Material given to copied faces that have none (used when
            exploding a painted group).
    """
    faces, edges, _, insts = split_targets(targets)
    out = Copied()
    specs = []
    for f in faces:
        loops = [[apply_point(matrix, v.position) for v in loop] for loop in f.loops]
        specs.append(
            FaceSpec(
                outer=loops[0],
                holes=loops[1:],
                normal=apply_normal(matrix, f.normal),
                material=f.material or inherit_material,
                back_material=f.back_material,
                tag=f.tag,
            )
        )
    made = dest.add_faces(specs) if specs else []
    for f, results in zip(faces, made, strict=True):
        for g in results:
            g.hidden = f.hidden
        out.faces.extend(results)
    face_edges = {e for f in faces for e in f.edges()}
    loose = [e for e in edges if e not in face_edges]
    if loose:
        dest.add_edges(
            [(apply_point(matrix, e.v1.position), apply_point(matrix, e.v2.position)) for e in loose],
            auto_faces=False,
        )
    curve_map: dict[int, int] = {}
    for e in list(face_edges) + loose:
        pieces = dest.edges_on_segment(apply_point(matrix, e.v1.position), apply_point(matrix, e.v2.position))
        for piece in pieces:
            piece.soft, piece.smooth, piece.hidden, piece.tag = e.soft, e.smooth, e.hidden, e.tag
            if e.curve is not None:
                piece.curve = curve_map.setdefault(e.curve, dest.registry.new_id())
        out.edges.extend(pieces)
    for inst in insts:
        copy = add_instance(dest, inst.definition, matrix @ inst.transform)
        copy.name, copy.material, copy.tag, copy.hidden = inst.name, inst.material, inst.tag, inst.hidden
        out.instances.append(copy)
    out.faces = list(dict.fromkeys(out.faces))
    out.edges = list(dict.fromkeys(out.edges))
    return out


def copy_entities(entities: Entities, targets: Sequence[Entity], matrix: np.ndarray) -> Copied:
    """Copy entities within their own collection (Move tool with Ctrl)."""
    return copy_geometry(entities, targets, entities, matrix)


def linear_array(
    entities: Entities, targets: Sequence[Entity], offset: np.ndarray, count: int
) -> list[Copied]:
    """Make ``count`` copies, the k-th displaced by ``k * offset`` (k = 1..count)."""
    return [copy_entities(entities, targets, translation(offset * k)) for k in range(1, count + 1)]


def bounds_of(targets: Iterable[Entity]) -> tuple[np.ndarray, np.ndarray] | None:
    """Bounding box of entities in their own collection's coordinates."""
    from pymodeler.core.components import entities_bounds

    pts: list[np.ndarray] = []
    for t in targets:
        if isinstance(t, Face):
            pts.extend(v.position for v in t.vertices())
        elif isinstance(t, Edge):
            pts.extend(v.position for v in t.vertices)
        elif isinstance(t, Vertex):
            pts.append(t.position)
        elif isinstance(t, ComponentInstance):
            box = entities_bounds(t.definition.entities, t.transform)
            if box is not None:
                pts.extend(box)
    if not pts:
        return None
    arr = np.array(pts)
    return arr.min(axis=0), arr.max(axis=0)
