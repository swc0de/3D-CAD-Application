"""Vertices, edges, faces and the :class:`Entities` collection that owns them.

This module holds the data model and the *low-level* primitives that keep the
topology consistent (vertex/edge/face links, the vertex spatial hash, the edge map).
The SketchUp-style "sticky" behaviour built on top of them lives in
:mod:`pymodeler.core.sticky`, mixed into :class:`Entities`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable, Iterator

import numpy as np

from pymodeler.core.changes import Registry
from pymodeler.core.planar import point_in_loops, polygon_centroid, triangulate
from pymodeler.core.spatial import BoxIndex
from pymodeler.core.sticky import StickyMixin
from pymodeler.core.vec import TOL, GeometryError, Plane, PointLike, newell_normal, norm, v3

if TYPE_CHECKING:
    from pymodeler.core.components import ComponentInstance

_CELL = 1.0
"""Cell size (mm) of the vertex spatial hash; must exceed ``TOL``."""


class Entity:
    """Base class of everything stored in an :class:`Entities` collection."""

    def __init__(self, parent: "Entities") -> None:
        self.parent: Entities | None = parent
        self.id: int = parent.registry.allocate(self)
        self.tag: str | None = None
        self.hidden: bool = False

    @property
    def alive(self) -> bool:
        """False once the entity has been erased."""
        return self.parent is not None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} #{self.id}>"


class Vertex(Entity):
    """A point shared by edges."""

    def __init__(self, parent: "Entities", position: PointLike) -> None:
        super().__init__(parent)
        p = v3(position)
        self._pos = p
        self._pos.flags.writeable = False
        self._t: tuple[float, float, float] = (float(p[0]), float(p[1]), float(p[2]))
        self.edges: dict[Edge, None] = {}
        self._slot = -1

    @property
    def position(self) -> np.ndarray:
        """The vertex position (read-only array; assign a new value to move it)."""
        return self._pos

    @position.setter
    def position(self, value: PointLike) -> None:
        if self.parent is None:
            raise GeometryError("cannot move an erased vertex")
        self.parent._move_vertex(self, value)

    @property
    def faces(self) -> list["Face"]:
        """Faces that use this vertex."""
        out: dict[Face, None] = {}
        for edge in self.edges:
            for face in edge.faces:
                out[face] = None
        return list(out)


class Edge(Entity):
    """A straight segment between two vertices."""

    def __init__(self, parent: "Entities", v1: Vertex, v2: Vertex) -> None:
        super().__init__(parent)
        self.v1 = v1
        self.v2 = v2
        self.faces: dict[Face, None] = {}
        self.soft = False
        self.smooth = False
        self.curve: int | None = None
        self._slot = -1

    def box(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Axis-aligned bounding box ``(lo, hi)`` of the edge."""
        a, b = self.v1._t, self.v2._t
        return (
            (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2])),
            (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2])),
        )

    @property
    def vertices(self) -> tuple[Vertex, Vertex]:
        """Both end vertices."""
        return self.v1, self.v2

    def other(self, v: Vertex) -> Vertex:
        """The end vertex that is not ``v``."""
        return self.v2 if v is self.v1 else self.v1

    def length(self) -> float:
        """Edge length in millimetres."""
        return math.dist(self.v1._t, self.v2._t)

    def direction(self) -> np.ndarray:
        """Unit vector from ``v1`` to ``v2``."""
        d = self.v2.position - self.v1.position
        return d / max(norm(d), 1e-12)

    def midpoint(self) -> np.ndarray:
        """Point halfway along the edge."""
        return (self.v1.position + self.v2.position) * 0.5

    def copy_attributes_from(self, other: "Edge") -> None:
        """Copy display attributes (soft/smooth/hidden/curve/tag) from ``other``."""
        self.soft = other.soft
        self.smooth = other.smooth
        self.hidden = other.hidden
        self.curve = other.curve
        self.tag = other.tag


class Face(Entity):
    """A planar polygon: one outer loop plus optional hole loops.

    The outer loop runs counter-clockwise around :attr:`normal` and holes run clockwise,
    so the front side faces along the normal.
    """

    def __init__(self, parent: "Entities", loops: list[list[Vertex]]) -> None:
        super().__init__(parent)
        self.loops = loops
        self.material: str | None = None
        self.back_material: str | None = None
        self._normal: np.ndarray | None = None
        self._plane: Plane | None = None

    # -- geometry -----------------------------------------------------------------
    @property
    def outer_loop(self) -> list[Vertex]:
        """Vertices of the outer boundary, counter-clockwise about the normal."""
        return self.loops[0]

    @property
    def inner_loops(self) -> list[list[Vertex]]:
        """Hole boundaries."""
        return self.loops[1:]

    @property
    def normal(self) -> np.ndarray:
        """Unit normal of the front side."""
        if self._normal is None:
            n = newell_normal([v._t for v in self.loops[0]])
            length = norm(n)
            if length < 1e-12:
                raise GeometryError(f"face #{self.id} is degenerate")
            self._normal = n / length
        return self._normal

    @property
    def plane(self) -> Plane:
        """The face's oriented plane."""
        if self._plane is None:
            self._plane = Plane.from_point_normal(self.loops[0][0].position, self.normal)
        return self._plane

    def invalidate(self) -> None:
        """Drop cached geometry after loops or vertices change."""
        self._normal = None
        self._plane = None

    def vertices(self) -> list[Vertex]:
        """All vertices of all loops."""
        return [v for loop in self.loops for v in loop]

    def loop_points(self) -> list[np.ndarray]:
        """Each loop as an (N, 3) array of positions."""
        return [np.array([v._t for v in loop]) for loop in self.loops]

    def edges(self) -> list[Edge]:
        """Edges bounding the face (outer loop first)."""
        assert self.parent is not None
        out: list[Edge] = []
        for loop in self.loops:
            count = len(loop)
            for i in range(count):
                e = self.parent.edge_between(loop[i], loop[(i + 1) % count])
                if e is not None:
                    out.append(e)
        return out

    def uses_directed(self, a: Vertex, b: Vertex) -> bool | None:
        """True if a loop runs ``a -> b``, False if ``b -> a``, None if neither."""
        for loop in self.loops:
            count = len(loop)
            for i in range(count):
                if loop[i] is a and loop[(i + 1) % count] is b:
                    return True
                if loop[i] is b and loop[(i + 1) % count] is a:
                    return False
        return None

    def loops_2d(self, plane: Plane | None = None) -> list[list[tuple[float, float]]]:
        """Loops projected to the 2D coordinates of ``plane`` (default: own plane)."""
        plane = plane or self.plane
        return [plane.to_2d([v._t for v in loop]) for loop in self.loops]

    def area(self) -> float:
        """Area in square millimetres (holes subtracted)."""
        n = self.normal
        outer = abs(float(newell_normal([v._t for v in self.loops[0]]) @ n)) * 0.5
        holes = sum(
            abs(float(newell_normal([v._t for v in loop]) @ n)) * 0.5 for loop in self.loops[1:]
        )
        return outer - holes

    def centroid(self) -> np.ndarray:
        """Area centroid of the face."""
        plane = self.plane
        loops = self.loops_2d(plane)
        return plane.to_3d(polygon_centroid(loops[0], loops[1:]))

    def triangles(self) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
        """Triangulate the face.

        Returns:
            ``(points, triangles)`` where ``points`` is an (N, 3) array of all loop
            vertices (outer first) and each triangle is counter-clockwise about the normal.
        """
        plane = self.plane
        loops = self.loops_2d(plane)
        tris = triangulate(loops[0], loops[1:])
        points = np.array([v._t for loop in self.loops for v in loop])
        return points, tris

    def contains_point(self, p: PointLike, tol: float = TOL) -> bool:
        """True if ``p`` lies on the face's plane and inside its boundary."""
        plane = self.plane
        if not plane.contains(p, tol):
            return False
        return point_in_loops(plane.to_2d([p])[0], self.loops_2d(plane))

    def reverse(self) -> None:
        """Flip the face so its back becomes its front (materials swap too)."""
        self.loops = [list(reversed(loop)) for loop in self.loops]
        self.material, self.back_material = self.back_material, self.material
        self.invalidate()
        if self.parent is not None:
            self.parent.registry.modified(self.id)

    def copy_attributes_from(self, other: "Face") -> None:
        """Copy materials, tag and visibility from ``other``."""
        self.material = other.material
        self.back_material = other.back_material
        self.tag = other.tag
        self.hidden = other.hidden


class Entities(StickyMixin):
    """A collection of geometry (model root, group or component definition).

    Geometry in one collection sticks together; geometry in different collections never
    interacts.  Instances of components and groups also live here.
    """

    def __init__(self, registry: Registry | None = None, owner: Any = None) -> None:
        self.registry = registry if registry is not None else Registry()
        self.owner = owner
        self.vertices: dict[int, Vertex] = {}
        self.edges: dict[int, Edge] = {}
        self.faces: dict[int, Face] = {}
        self.instances: dict[int, "ComponentInstance"] = {}
        self._edge_map: dict[tuple[int, int], Edge] = {}
        self._grid: dict[tuple[int, int, int], list[Vertex]] = {}
        self._vertex_index: BoxIndex[Vertex] = BoxIndex()
        self._edge_index: BoxIndex[Edge] = BoxIndex()

    # -- queries --------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.vertices) + len(self.edges) + len(self.faces) + len(self.instances)

    @property
    def is_empty(self) -> bool:
        """True if the collection holds no geometry and no instances."""
        return not (self.edges or self.faces or self.instances or self.vertices)

    def find_vertex(self, p: PointLike, tol: float = TOL) -> Vertex | None:
        """The vertex within ``tol`` of ``p``, if any."""
        x, y, z = float(p[0]), float(p[1]), float(p[2])
        cx, cy, cz = math.floor(x / _CELL), math.floor(y / _CELL), math.floor(z / _CELL)
        best: Vertex | None = None
        best_d = tol * tol
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for k in (cz - 1, cz, cz + 1):
                    for v in self._grid.get((i, j, k), ()):
                        vx, vy, vz = v._t
                        d = (vx - x) ** 2 + (vy - y) ** 2 + (vz - z) ** 2
                        if d <= best_d:
                            best, best_d = v, d
        return best

    def edge_between(self, a: Vertex, b: Vertex) -> Edge | None:
        """The edge joining two vertices, if any."""
        key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        return self._edge_map.get(key)

    def iter_entities(self) -> Iterator[Entity]:
        """All entities: faces, edges, vertices, then instances."""
        yield from self.faces.values()
        yield from self.edges.values()
        yield from self.vertices.values()
        yield from self.instances.values()  # type: ignore[misc]

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Bounding box of the raw geometry (instances excluded), or ``None`` if empty."""
        if not self.vertices:
            return None
        pts = np.array([v._t for v in self.vertices.values()])
        return pts.min(axis=0), pts.max(axis=0)

    def edges_near(self, lo: Iterable[float], hi: Iterable[float]) -> list[Edge]:
        """Edges whose bounding boxes overlap the box ``lo..hi``."""
        return self._edge_index.query(tuple(lo), tuple(hi))

    def vertices_near(self, lo: Iterable[float], hi: Iterable[float]) -> list[Vertex]:
        """Vertices inside the box ``lo..hi``."""
        return self._vertex_index.query(tuple(lo), tuple(hi))

    def vertices_on_plane(self, plane: Plane, tol: float = TOL) -> list[Vertex]:
        """Vertices within ``tol`` of ``plane``."""
        return self._vertex_index.query_plane(plane.normal, plane.offset, tol)

    def faces_in_plane(self, plane: Plane, tol: float = TOL) -> list[Face]:
        """Faces whose outer loop lies in ``plane`` (either orientation)."""
        out = []
        for face in self.faces.values():
            if all(abs(plane.distance(v._t)) <= tol for v in face.loops[0]):
                out.append(face)
        return out

    # -- low-level creation -----------------------------------------------------------
    def _grid_key(self, t: tuple[float, float, float]) -> tuple[int, int, int]:
        return (math.floor(t[0] / _CELL), math.floor(t[1] / _CELL), math.floor(t[2] / _CELL))

    def _create_vertex(self, p: PointLike) -> Vertex:
        """Create a vertex without merging (callers ensure it is not a duplicate)."""
        v = Vertex(self, p)
        self.vertices[v.id] = v
        self._grid.setdefault(self._grid_key(v._t), []).append(v)
        v._slot = self._vertex_index.add(v, v._t, v._t)
        return v

    def _create_edge(self, a: Vertex, b: Vertex, template: Edge | None = None) -> Edge:
        """Create an edge between two distinct vertices that are not yet joined."""
        if a is b:
            raise GeometryError("an edge needs two distinct vertices")
        key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if key in self._edge_map:
            raise GeometryError("vertices are already joined by an edge")
        e = Edge(self, a, b)
        if template is not None:
            e.copy_attributes_from(template)
        self.edges[e.id] = e
        self._edge_map[key] = e
        a.edges[e] = None
        b.edges[e] = None
        e._slot = self._edge_index.add(e, *e.box())
        return e

    def _create_face(self, loops: list[list[Vertex]], template: Face | None = None) -> Face:
        """Create a face from vertex loops whose edges already exist."""
        f = Face(self, loops)
        if template is not None:
            f.copy_attributes_from(template)
        self.faces[f.id] = f
        self._attach_face(f)
        return f

    def _attach_face(self, f: Face) -> None:
        """Link a face to its bounding edges."""
        f.invalidate()
        for loop in f.loops:
            count = len(loop)
            for i in range(count):
                e = self.edge_between(loop[i], loop[(i + 1) % count])
                if e is None:
                    raise GeometryError(f"face #{f.id} references a missing edge")
                e.faces[f] = None

    def _detach_face(self, f: Face) -> None:
        """Unlink a face from its edges (tolerates edges that are already gone)."""
        for e in self._loop_edges(f):
            e.faces.pop(f, None)

    def _loop_edges(self, f: Face) -> list[Edge]:
        """Existing edges along a face's loops."""
        out = []
        for loop in f.loops:
            count = len(loop)
            for i in range(count):
                e = self.edge_between(loop[i], loop[(i + 1) % count])
                if e is not None:
                    out.append(e)
        return out

    def _discard_face(self, f: Face) -> None:
        """Erase a face (its edges stay)."""
        self._detach_face(f)
        self.faces.pop(f.id, None)
        self.registry.release(f.id)
        f.parent = None

    def _discard_edge(self, e: Edge) -> None:
        """Erase an edge; faces that use it must be handled by the caller."""
        for f in list(e.faces):
            e.faces.pop(f, None)
        key = (e.v1.id, e.v2.id) if e.v1.id < e.v2.id else (e.v2.id, e.v1.id)
        if self._edge_map.get(key) is e:
            del self._edge_map[key]
        e.v1.edges.pop(e, None)
        e.v2.edges.pop(e, None)
        self.edges.pop(e.id, None)
        self._edge_index.remove(e._slot)
        self.registry.release(e.id)
        e.parent = None

    def _discard_vertex(self, v: Vertex) -> None:
        """Erase a vertex that no edge uses any more."""
        if v.edges:
            raise GeometryError("cannot discard a vertex that still has edges")
        bucket = self._grid.get(self._grid_key(v._t))
        if bucket is not None and v in bucket:
            bucket.remove(v)
            if not bucket:
                del self._grid[self._grid_key(v._t)]
        self.vertices.pop(v.id, None)
        self._vertex_index.remove(v._slot)
        self.registry.release(v.id)
        v.parent = None

    def _reconnect_edge(self, e: Edge, a: Vertex, b: Vertex) -> None:
        """Re-point an existing edge at new end vertices (keeps its id)."""
        old_key = (e.v1.id, e.v2.id) if e.v1.id < e.v2.id else (e.v2.id, e.v1.id)
        if self._edge_map.get(old_key) is e:
            del self._edge_map[old_key]
        e.v1.edges.pop(e, None)
        e.v2.edges.pop(e, None)
        e.v1, e.v2 = a, b
        key = (a.id, b.id) if a.id < b.id else (b.id, a.id)
        if key in self._edge_map:
            raise GeometryError("reconnecting would duplicate an existing edge")
        self._edge_map[key] = e
        a.edges[e] = None
        b.edges[e] = None
        self._edge_index.update(e._slot, *e.box())
        self.registry.modified(e.id)

    def _move_vertex(self, v: Vertex, value: PointLike) -> None:
        """Move a vertex and update the spatial hash and cached face data."""
        old_key = self._grid_key(v._t)
        bucket = self._grid.get(old_key)
        if bucket is not None and v in bucket:
            bucket.remove(v)
            if not bucket:
                del self._grid[old_key]
        p = v3(value)
        p.flags.writeable = False
        v._pos = p
        v._t = (float(p[0]), float(p[1]), float(p[2]))
        self._grid.setdefault(self._grid_key(v._t), []).append(v)
        self._vertex_index.update(v._slot, v._t, v._t)
        for e in v.edges:
            self._edge_index.update(e._slot, *e.box())
            for f in e.faces:
                f.invalidate()
        self.registry.modified(v.id)

    def remove_orphan_vertices(self, candidates: Iterable[Vertex] | None = None) -> int:
        """Erase vertices that have no edges; returns how many were removed."""
        pool = list(candidates) if candidates is not None else list(self.vertices.values())
        count = 0
        for v in pool:
            if v.parent is self and not v.edges:
                self._discard_vertex(v)
                count += 1
        return count

    # -- convenience --------------------------------------------------------------------
    def total_face_area(self) -> float:
        """Sum of all face areas."""
        return sum(f.area() for f in self.faces.values())
