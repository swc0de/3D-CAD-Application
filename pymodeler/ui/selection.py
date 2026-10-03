"""Selection: what is selected, how clicks and rectangles pick it (no Qt dependency).

SketchUp rules: clicking picks an edge (preferred when close) or a face; clicking a
group or component from outside picks the whole instance. Double-click selects a face
with its edges (or an edge with its faces); triple-click selects everything connected.
Dragging left-to-right selects what is fully inside the rectangle (window); dragging
right-to-left also selects what it touches (crossing).
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator

import numpy as np

from pymodeler.core.components import ComponentInstance, entities_bounds
from pymodeler.core.entities import Edge, Entities, Entity, Face, Vertex
from pymodeler.render.camera import Camera
from pymodeler.ui.navigation import pixel_ray
from pymodeler.ui.picking import EDGE_TOLERANCE, Hit, PickScene


class Selection:
    """An ordered set of selected entities with change notifications."""

    def __init__(self) -> None:
        self._items: dict[Entity, None] = {}
        self.listeners: list[Callable[["Selection"], None]] = []

    def __iter__(self) -> Iterator[Entity]:
        return iter([e for e in self._items if e.alive])

    def __len__(self) -> int:
        return len([e for e in self._items if e.alive])

    def __contains__(self, entity: object) -> bool:
        return entity in self._items

    def items(self) -> list[Entity]:
        """Live selected entities."""
        return list(self)

    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener(self)

    def set(self, entities: Iterable[Entity]) -> None:
        self._items = dict.fromkeys(entities)
        self._changed()

    def add(self, entities: Iterable[Entity]) -> None:
        self._items.update(dict.fromkeys(entities))
        self._changed()

    def remove(self, entities: Iterable[Entity]) -> None:
        for e in entities:
            self._items.pop(e, None)
        self._changed()

    def toggle(self, entities: Iterable[Entity]) -> None:
        for e in entities:
            if e in self._items:
                del self._items[e]
            else:
                self._items[e] = None
        self._changed()

    def clear(self) -> None:
        if self._items:
            self._items = {}
            self._changed()

    def describe(self) -> str:
        """Status-bar summary like ``"2 faces, 1 group"``."""
        counts: dict[str, int] = {}
        for e in self:
            if isinstance(e, ComponentInstance):
                key = "group" if e.is_group else "component"
            else:
                key = type(e).__name__.lower()
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return "Nothing selected"
        return ", ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in counts.items()) + " selected"


def top_level(hit: Hit, active: Entities) -> Entity | None:
    """The entity of the active context that a hit belongs to (an instance if the hit
    is inside a group or component)."""
    entity = hit.entity
    if entity.parent is active:
        return entity
    for inst in hit.path:
        if inst.parent is active:
            return inst
    return None


def pick_entity(scene: PickScene, camera: Camera, x: float, y: float, width: int, height: int,
                active: Entities, prefer_faces: bool = False) -> Entity | None:
    """The entity of the active context under the cursor (edges win when close)."""
    origin, direction = pixel_ray(camera, x, y, width, height)
    faces = scene.ray_faces(origin, direction)
    front = faces[0].depth if faces else np.inf
    if not prefer_faces:
        for hit in scene.near_edges(camera, x, y, width, height, EDGE_TOLERANCE):
            if hit.depth <= front * 1.001 + 1.0:
                entity = top_level(hit, active)
                if entity is not None:
                    return entity
    for hit in faces:
        entity = top_level(hit, active)
        if entity is not None:
            return entity
    return None


def pick_face(scene: PickScene, camera: Camera, x: float, y: float, width: int, height: int,
              active: Entities) -> Hit | None:
    """The front-most face of the active context under the cursor."""
    origin, direction = pixel_ray(camera, x, y, width, height)
    for hit in scene.ray_faces(origin, direction):
        if hit.entity.parent is active:
            return hit
        if top_level(hit, active) is not None:
            return None  # a group is in front of the active context's faces
    return None


def expand_double(entity: Entity) -> list[Entity]:
    """Double-click: a face with its edges, or an edge with its faces."""
    if isinstance(entity, Face):
        return [entity, *entity.edges()]
    if isinstance(entity, Edge):
        return [entity, *entity.faces]
    return [entity]


def connected(entity: Entity) -> list[Entity]:
    """Triple-click: every face and edge connected to the entity."""
    if not isinstance(entity, (Face, Edge, Vertex)):
        return [entity]
    seen_v: set[Vertex] = set()
    if isinstance(entity, Face):
        stack = list(entity.vertices())
    elif isinstance(entity, Edge):
        stack = [entity.v1, entity.v2]
    else:
        stack = [entity]
    edges: dict[Edge, None] = {}
    faces: dict[Face, None] = {}
    while stack:
        v = stack.pop()
        if v in seen_v:
            continue
        seen_v.add(v)
        for e in v.edges:
            edges[e] = None
            faces.update(dict.fromkeys(e.faces))
            stack.append(e.other(v))
    return [*faces, *edges]


def rectangle_select(
    scene: PickScene, camera: Camera, rect: tuple[float, float, float, float], width: int, height: int,
    active: Entities, crossing: bool, world: np.ndarray | None = None,
) -> list[Entity]:
    """Entities of the active context inside (window) or touching (crossing) a screen rectangle.

    ``rect`` is ``(x0, y0, x1, y1)`` in pixels (any corner order).
    """
    x0, x1 = sorted((rect[0], rect[2]))
    y0, y1 = sorted((rect[1], rect[3]))
    world = np.eye(4) if world is None else world

    def screen(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        proj = scene.project(camera, points @ world[:3, :3].T + world[:3, 3], width, height)
        inside = proj.visible & (proj.xy[:, 0] >= x0) & (proj.xy[:, 0] <= x1) & (proj.xy[:, 1] >= y0) & (proj.xy[:, 1] <= y1)
        return proj.xy, inside

    def accept(points: np.ndarray) -> bool:
        xy, inside = screen(points)
        if not crossing:
            return bool(inside.all())
        if inside.any():
            return True
        return _polyline_crosses(xy, (x0, y0, x1, y1))

    out: list[Entity] = []
    for f in active.faces.values():
        pts = np.array([v._t for v in f.outer_loop])
        if accept(pts) or (crossing and _rect_inside_face(f, camera, (x0, y0, x1, y1), width, height, world)):
            out.append(f)
    for e in active.edges.values():
        if accept(np.array([e.v1._t, e.v2._t])):
            out.append(e)
    for inst in active.instances.values():
        box = entities_bounds(inst.definition.entities, world @ inst.transform)
        if box is None:
            continue
        lo, hi = box
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        if accept(corners):
            out.append(inst)
    return out


def _polyline_crosses(xy: np.ndarray, rect: tuple[float, float, float, float]) -> bool:
    """Whether a closed screen polyline crosses the rectangle's border."""
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    sides = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    n = len(xy)
    for i in range(n if n > 2 else 1):
        a, b = xy[i], xy[(i + 1) % n]
        for c, d in sides:
            if _segments_intersect(a, b, np.array(c), np.array(d)):
                return True
    return False


def _segments_intersect(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> bool:
    def orient(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> float:
        return float((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _rect_inside_face(face: Face, camera: Camera, rect: tuple[float, float, float, float], width: int, height: int,
                      world: np.ndarray) -> bool:
    """Whether the rectangle's centre lies on the face (crossing selection inside a face)."""
    from pymodeler.core.transform import apply_point, apply_vector, inverse

    x = (rect[0] + rect[2]) / 2
    y = (rect[1] + rect[3]) / 2
    origin, direction = pixel_ray(camera, x, y, width, height)
    inv = inverse(world)
    origin, direction = apply_point(inv, origin), apply_vector(inv, direction)
    plane = face.plane
    denom = float(plane.normal @ direction)
    if abs(denom) < 1e-12:
        return False
    t = -plane.distance(origin) / denom
    return t > 0 and face.contains_point(origin + direction * t, tol=1.0)
