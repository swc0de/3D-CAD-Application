"""Face selection by direction, position, size or index (used by build scripts).

A *selector* picks faces out of a set of entities, looking inside groups and
component instances too.  Direction selectors (``"top"``, ``"+x"``, ``{"normal": ...}``)
choose the faces facing that way that are furthest along it, so ``"top"`` on a box is
its lid.  A lone face drawn on its own is two-sided for selection purposes: asking for
its back side returns it with ``flip=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from pymodeler.core.components import ComponentInstance, context_world
from pymodeler.core.entities import Entity, Face
from pymodeler.core.transform import apply_normal, apply_point
from pymodeler.core.vec import GeometryError, normalize, v3

DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "top": (0, 0, 1), "up": (0, 0, 1), "+z": (0, 0, 1), "z": (0, 0, 1),
    "bottom": (0, 0, -1), "down": (0, 0, -1), "-z": (0, 0, -1),
    "right": (1, 0, 0), "+x": (1, 0, 0), "x": (1, 0, 0),
    "left": (-1, 0, 0), "-x": (-1, 0, 0),
    "back": (0, 1, 0), "+y": (0, 1, 0), "y": (0, 1, 0),
    "front": (0, -1, 0), "-y": (0, -1, 0),
}
"""Named directions accepted as face selectors (Z up, front faces -Y)."""

ALIGN_COS = 0.9998
"""Cosine threshold (about 1 degree) for a face to count as facing a direction."""


@dataclass
class Picked:
    """A selected face with its world placement."""

    face: Face
    world: np.ndarray
    flip: bool = False

    @property
    def normal(self) -> np.ndarray:
        """World-space front normal (reversed when ``flip`` is set)."""
        n = apply_normal(self.world, self.face.normal)
        return -n if self.flip else n

    @property
    def centroid(self) -> np.ndarray:
        """World-space centroid."""
        return apply_point(self.world, self.face.centroid())

    def is_free(self) -> bool:
        """True if no edge of the face is shared with another face."""
        return all(len(e.faces) == 1 for e in self.face.edges())


def candidate_faces(entities: Iterable[Entity]) -> list[Picked]:
    """All faces in a set of entities, including those inside instances (recursively)."""
    out: list[Picked] = []
    seen: set[int] = set()
    for ent in entities:
        if isinstance(ent, Face) and ent.parent is not None and ent.id not in seen:
            seen.add(ent.id)
            out.append(Picked(ent, context_world(ent.parent)))
        elif isinstance(ent, ComponentInstance) and ent.parent is not None:
            _collect_instance(ent, context_world(ent.parent) @ ent.transform, out, 0)
    return out


def _collect_instance(inst: ComponentInstance, world: np.ndarray, out: list[Picked], depth: int) -> None:
    """Add every face inside an instance."""
    if depth > 32:
        return
    ents = inst.definition.entities
    out.extend(Picked(f, world) for f in ents.faces.values())
    for child in ents.instances.values():
        _collect_instance(child, world @ child.transform, out, depth + 1)


def select_faces(candidates: Sequence[Picked], selector: Any = None) -> list[Picked]:
    """Apply a selector to candidate faces.

    Selectors: ``None`` (the only face, error if several), ``"all"``, a direction name
    (see :data:`DIRECTIONS`), ``"largest"``, ``"smallest"``, ``{"normal": [x, y, z]}``,
    ``{"near": [x, y, z]}`` or ``{"index": n}``.

    Raises:
        GeometryError: if nothing matches or the selector is malformed.
    """
    if not candidates:
        raise GeometryError("the target has no faces")
    if selector is None:
        if len(candidates) == 1:
            return list(candidates)
        raise GeometryError(
            f"the target has {len(candidates)} faces; add a 'face' selector such as \"top\""
        )
    if isinstance(selector, str):
        key = selector.strip().lower()
        if key == "all":
            return list(candidates)
        if key in ("largest", "smallest"):
            pick = max if key == "largest" else min
            return [pick(candidates, key=lambda p: p.face.area())]
        if key in DIRECTIONS:
            return _by_direction(candidates, np.array(DIRECTIONS[key], dtype=float))
        raise GeometryError(
            f"unknown face selector {selector!r} (use top, bottom, front, back, left, right, "
            "+x, -x, +y, -y, +z, -z, all, largest, smallest)"
        )
    if isinstance(selector, dict):
        if "normal" in selector:
            return _by_direction(candidates, normalize(v3(selector["normal"])))
        if "near" in selector:
            point = v3(selector["near"])
            return [min(candidates, key=lambda p: _distance_to_face(p, point))]
        if "index" in selector:
            idx = int(selector["index"])
            if not -len(candidates) <= idx < len(candidates):
                raise GeometryError(f"face index {idx} out of range (target has {len(candidates)} faces)")
            return [candidates[idx]]
    raise GeometryError(f"invalid face selector {selector!r}")


def _by_direction(candidates: Sequence[Picked], direction: np.ndarray) -> list[Picked]:
    """Faces facing ``direction`` that lie furthest along it."""
    matches = [p for p in candidates if float(p.normal @ direction) >= ALIGN_COS]
    if not matches:
        flipped = [Picked(p.face, p.world, flip=True) for p in candidates
                   if float(p.normal @ direction) <= -ALIGN_COS and p.is_free()]
        matches = flipped
    if not matches:
        facing = ", ".join(_describe(p.normal) for p in candidates[:6])
        raise GeometryError(
            f"no face faces {_describe(direction)} (target faces point: {facing})"
        )
    levels = [float(p.centroid @ direction) for p in matches]
    best = max(levels)
    return [p for p, lvl in zip(matches, levels, strict=True) if lvl >= best - 0.01]


def _distance_to_face(p: Picked, point: np.ndarray) -> tuple[int, float]:
    """Sort key for ``near``: faces whose area contains the point's projection come
    first (by distance to their plane), then the rest by distance to their edges."""
    from pymodeler.core.transform import inverse
    from pymodeler.core.vec import project_point_to_segment

    local = apply_point(inverse(p.world), point)
    plane = p.face.plane
    if p.face.contains_point(plane.project(local), tol=1.0):
        return 0, abs(plane.distance(local))
    best = float("inf")
    for e in p.face.edges():
        t, _ = project_point_to_segment(local, e.v1._t, e.v2._t)
        t = min(1.0, max(0.0, t))
        closest = e.v1.position + (e.v2.position - e.v1.position) * t
        best = min(best, float(np.linalg.norm(local - closest)))
    return 1, best


def _describe(n: np.ndarray) -> str:
    """Readable name for a direction vector."""
    for name in ("+x", "-x", "+y", "-y", "+z", "-z"):
        if float(n @ np.array(DIRECTIONS[name])) >= ALIGN_COS:
            return name
    return "[" + ", ".join(f"{c:.2f}" for c in n) + "]"
