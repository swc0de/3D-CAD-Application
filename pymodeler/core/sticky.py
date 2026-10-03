"""SketchUp-style "sticky" geometry, mixed into :class:`~pymodeler.core.entities.Entities`.

The central idea is the **plane re-solve** (:meth:`StickyMixin.resolve_plane`): after
edges change in a plane, every edge lying in that plane is projected to 2D, the bounded
regions they enclose are computed, and each region is decided:

* inside an existing face of that plane -> it stays a face and inherits that face's
  orientation and materials (this is how faces split, heal and merge);
* inside an explicitly requested face -> it becomes a face;
* in *cancel* mode, a requested face lying on an existing face that points the
  opposite way removes both (push/pull uses this to punch holes and shorten solids);
* bounded by a newly drawn edge (``auto_faces``) -> a new face, so drawing a closed
  loop of coplanar edges creates a face.

Edges are always split where they cross, touch or overlap, so the 2D graph handed to
:func:`~pymodeler.core.planar.find_regions` never has crossings.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Callable, Iterable, Sequence

import numpy as np

from pymodeler.core.planar import (
    Point2,
    Region,
    find_regions,
    interior_point,
    point_in_loops,
    signed_area,
)
from pymodeler.core.vec import (
    TOL,
    GeometryError,
    Plane,
    PointLike,
    newell_normal,
    plane_key,
    point_on_segment_interior,
    project_point_to_segment,
    segment_intersection,
    v3,
)

if TYPE_CHECKING:
    from pymodeler.core.changes import Registry
    from pymodeler.core.entities import Edge, Entity, Face, Vertex


@dataclass
class FaceSpec:
    """A request to create a face from explicit loops of points."""

    outer: Sequence[PointLike]
    holes: Sequence[Sequence[PointLike]] = ()
    normal: PointLike | None = None
    """Desired front direction; defaults to the right-hand rule on ``outer``."""
    material: str | None = None
    back_material: str | None = None
    tag: str | None = None
    cancels: bool = False
    """In cancel mode, remove both this face and an opposite-facing face it lands on."""

    def plane(self) -> Plane:
        """The plane of the outer loop, oriented by :attr:`normal` when given."""
        plane = Plane.from_points([v3(p) for p in self.outer])
        if self.normal is not None and float(np.dot(plane.normal, v3(self.normal))) < 0:
            plane = plane.flipped()
        return plane


@dataclass
class _Seed:
    """A face (existing or requested) used to decide which regions are faces."""

    loops: list[list[Point2]]
    sign: int
    area: float
    face: "Face | None" = None
    request: int | None = None
    cancels: bool = False


class StickyMixin:
    """High-level, topology-aware editing methods for :class:`Entities`."""

    # Attributes provided by Entities (declared for type checkers).
    if TYPE_CHECKING:
        registry: Registry
        vertices: dict[int, Vertex]
        edges: dict[int, Edge]
        faces: dict[int, Face]

        def find_vertex(self, p: PointLike, tol: float = TOL) -> Vertex | None: ...
        def edge_between(self, a: Vertex, b: Vertex) -> Edge | None: ...
        def _create_vertex(self, p: PointLike) -> Vertex: ...
        def _create_edge(self, a: Vertex, b: Vertex, template: Edge | None = None) -> Edge: ...
        def _create_face(self, loops: list[list[Vertex]], template: Face | None = None) -> Face: ...
        def _attach_face(self, f: Face) -> None: ...
        def _detach_face(self, f: Face) -> None: ...
        def _discard_face(self, f: Face) -> None: ...
        def _discard_edge(self, e: Edge) -> None: ...
        def _discard_vertex(self, v: Vertex) -> None: ...
        def _reconnect_edge(self, e: Edge, a: Vertex, b: Vertex) -> None: ...
        def remove_orphan_vertices(self, candidates: Iterable[Vertex] | None = None) -> int: ...

    # ------------------------------------------------------------------ adding geometry
    def add_vertex(self, p: PointLike) -> "Vertex":
        """Return the vertex at ``p``, creating it unless one already exists there."""
        return self.find_vertex(p) or self._create_vertex(p)

    def add_line(self, p1: PointLike, p2: PointLike) -> list["Edge"]:
        """Draw a line; returns the edges that now cover it (it may have been split)."""
        return self.add_edges([(p1, p2)])

    def add_polyline(self, points: Sequence[PointLike], closed: bool = False) -> list["Edge"]:
        """Draw connected lines through ``points`` (closing the loop if ``closed``)."""
        pts = [v3(p) for p in points]
        segments = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        if closed and len(pts) > 2:
            segments.append((pts[-1], pts[0]))
        return self.add_edges(segments)

    def add_edges(
        self, segments: Iterable[tuple[PointLike, PointLike]], auto_faces: bool = True
    ) -> list["Edge"]:
        """Insert line segments with full sticky behaviour.

        Segments are split where they cross or touch existing geometry, existing edges
        are split likewise, faces are split by edges drawn across them, and (with
        ``auto_faces``) any coplanar loop closed by the new edges becomes a face.

        Returns:
            The edges covering the inserted segments, in order.
        """
        created: dict[Edge, None] = {}
        covering: list[Edge] = []
        for p, q in segments:
            covering.extend(self._insert_segment(v3(p), v3(q), created))
        for plane in self._candidate_planes(list(created), auto_faces):
            self.resolve_plane(plane, created, (), auto_faces=auto_faces)
        seen: dict[Edge, None] = {}
        for e in covering:
            if e.parent is self:
                seen[e] = None
        return list(seen)

    def add_face(
        self,
        outer: Sequence[PointLike],
        holes: Sequence[Sequence[PointLike]] = (),
        *,
        normal: PointLike | None = None,
        material: str | None = None,
        back_material: str | None = None,
    ) -> list["Face"]:
        """Create a face from explicit loops (its edges are added with sticky rules).

        Returns:
            The face(s) covering the requested area (more than one if existing edges
            cross it).

        Raises:
            GeometryError: if the outer loop is degenerate or not planar.
        """
        spec = FaceSpec(outer, holes, normal, material, back_material)
        return self.add_faces([spec])[0]

    def add_faces(self, specs: Sequence[FaceSpec], cancel_opposite: bool = False) -> list[list["Face"]]:
        """Create several faces in one batch (see :meth:`add_face`).

        Args:
            specs: Face requests.
            cancel_opposite: Enable push/pull cancellation for specs marked ``cancels``.

        Returns:
            For each spec, the faces now covering it.
        """
        planes = [self._check_spec(spec) for spec in specs]
        created: dict[Edge, None] = {}
        for spec in specs:
            for loop in [spec.outer, *spec.holes]:
                pts = [v3(p) for p in loop]
                for i in range(len(pts)):
                    self._insert_segment(pts[i], pts[(i + 1) % len(pts)], created)
        results: list[list[Face]] = [[] for _ in specs]
        for plane in self._candidate_planes(list(created), False, extra=planes):
            idx = [i for i, pl in enumerate(planes) if pl.same_plane(plane)]
            out = self.resolve_plane(
                plane, created, [specs[i] for i in idx], cancel_opposite=cancel_opposite
            )
            for j, i in enumerate(idx):
                results[i].extend(out[j])
        return [[f for f in dict.fromkeys(faces) if f.parent is self] for faces in results]

    @staticmethod
    def _check_spec(spec: FaceSpec) -> Plane:
        """Validate a face request and return its plane."""
        if len(spec.outer) < 3:
            raise GeometryError("a face needs at least three points")
        plane = spec.plane()
        for loop in [spec.outer, *spec.holes]:
            for p in loop:
                if abs(plane.distance(v3(p))) > TOL * 10:
                    raise GeometryError("face points are not coplanar")
        return plane

    # ------------------------------------------------------------------ splitting
    def split_edge(self, edge: "Edge", vertex: "Vertex") -> "Edge":
        """Split ``edge`` at ``vertex`` (which must lie on it).

        The original edge keeps its id and becomes the first piece; faces using it get
        the vertex inserted into their loops.

        Returns:
            The newly created second piece.
        """
        p, q = edge.v1, edge.v2
        if vertex is p or vertex is q:
            return edge
        faces = list(edge.faces)
        self._reconnect_edge(edge, p, vertex)
        piece = self._create_edge(vertex, q, template=edge)
        for f in faces:
            for loop in f.loops:
                count = len(loop)
                hit = next(
                    (i for i in range(count)
                     if {id(loop[i]), id(loop[(i + 1) % count])} == {id(p), id(q)}),
                    None,
                )
                if hit is not None:
                    loop.insert(hit + 1, vertex)
                    break
            piece.faces[f] = None
            f.invalidate()
        self.registry.replaced(edge.id, [edge.id, piece.id])
        return piece

    def _insert_segment(
        self, p: np.ndarray, q: np.ndarray, created: dict["Edge", None]
    ) -> list["Edge"]:
        """Insert one segment, splitting it and existing edges at every contact."""
        a = self.add_vertex(p)
        b = self.add_vertex(q)
        if a is b:
            return []
        # A: endpoints landing on the interior of existing edges split those edges.
        for w in (a, b):
            e = self._edge_containing(w)
            if e is not None:
                self.split_edge(e, w)
        # B: proper crossings create a vertex and split the crossed edge.
        lo, hi = _bbox(a._t, b._t)
        length = float(np.linalg.norm(np.subtract(b._t, a._t)))
        for e in list(self.edges.values()):
            if e.parent is not self or e.v1 in (a, b) or e.v2 in (a, b):
                continue
            if not _bbox_overlap(lo, hi, e.v1._t, e.v2._t):
                continue
            hit = segment_intersection(a._t, b._t, e.v1._t, e.v2._t)
            if hit is None:
                continue
            s, t, x = hit
            elen = e.length()
            if t * elen <= TOL or (1 - t) * elen <= TOL:
                continue  # touches an endpoint of e: picked up by step C
            if s * length <= TOL or (1 - s) * length <= TOL:
                continue  # our endpoint lies on e: handled by step A
            self.split_edge(e, self.add_vertex(x))
        # C: every vertex on the segment's interior becomes a split point.
        stops: list[tuple[float, Vertex]] = [(0.0, a), (1.0, b)]
        for v in self.vertices.values():
            if v is a or v is b or not _point_in_bbox(v._t, lo, hi):
                continue
            t, dist = project_point_to_segment(v._t, a._t, b._t)
            if dist <= TOL and t * length > TOL and (1 - t) * length > TOL:
                stops.append((t, v))
        stops.sort(key=lambda item: item[0])
        chain: list[Edge] = []
        for (_, v0), (_, v1) in pairwise(stops):
            if v0 is v1:
                continue
            e = self.edge_between(v0, v1)
            if e is None:
                e = self._create_edge(v0, v1)
                created[e] = None
            chain.append(e)
        return chain

    def _edge_containing(self, w: "Vertex") -> "Edge | None":
        """An edge whose interior passes through vertex ``w``, if any."""
        lo = (w._t[0] - TOL, w._t[1] - TOL, w._t[2] - TOL)
        hi = (w._t[0] + TOL, w._t[1] + TOL, w._t[2] + TOL)
        for e in self.edges.values():
            if e.v1 is w or e.v2 is w or not _bbox_overlap(lo, hi, e.v1._t, e.v2._t):
                continue
            if point_on_segment_interior(w._t, e.v1._t, e.v2._t):
                return e
        return None

    # ------------------------------------------------------------------ plane solving
    def _candidate_planes(
        self, new_edges: list["Edge"], auto_faces: bool, extra: Sequence[Plane] = ()
    ) -> list[Plane]:
        """Planes that may need re-solving after ``new_edges`` were inserted."""
        planes: dict[tuple[float, ...], Plane] = {}

        def add(pl: Plane) -> None:
            planes.setdefault(plane_key(pl), pl)

        for pl in extra:
            add(pl)
        if not new_edges:
            return list(planes.values())
        face_planes: dict[tuple[float, ...], Plane] = {}
        for f in self.faces.values():
            try:
                pl = f.plane
            except GeometryError:
                continue
            face_planes.setdefault(plane_key(pl), pl)
        for key, pl in face_planes.items():
            if key in planes:
                continue
            for e in new_edges:
                if pl.contains(e.v1._t) and pl.contains(e.v2._t):
                    add(pl)
                    break
        if auto_faces:
            for e in new_edges:
                if e.parent is not self:
                    continue
                for v in (e.v1, e.v2):
                    for e2 in v.edges:
                        if e2 is e:
                            continue
                        pl = Plane.from_three_points(e.v1._t, e.v2._t, e2.other(v)._t)
                        if pl is not None:
                            add(pl)
        return list(planes.values())

    def resolve_plane(
        self,
        plane: Plane,
        new_edges: Iterable["Edge"] = (),
        requests: Sequence[FaceSpec] = (),
        *,
        auto_faces: bool = False,
        cancel_opposite: bool = False,
    ) -> list[list["Face"]]:
        """Rebuild the faces of one plane from the edges lying in it.

        Args:
            plane: The plane to solve (orientation does not matter).
            new_edges: Edges added by the current operation (for ``auto_faces``).
            requests: Explicit faces to create in this plane.
            auto_faces: Make faces of regions bounded by a new edge.
            cancel_opposite: Remove regions where a cancelling request meets an
                existing face of opposite orientation.

        Returns:
            For each request, the faces now covering it.
        """
        plane = plane.canonical()
        result: list[list[Face]] = [[] for _ in requests]
        on = {vid: v for vid, v in self.vertices.items() if abs(plane.distance(v._t)) <= TOL}
        if len(on) < 3:
            return result
        u, w = plane.basis()
        origin = plane.origin()

        def proj(t: Sequence[float]) -> Point2:
            d = (t[0] - origin[0], t[1] - origin[1], t[2] - origin[2])
            return (
                d[0] * u[0] + d[1] * u[1] + d[2] * u[2],
                d[0] * w[0] + d[1] * w[1] + d[2] * w[2],
            )

        pts = {vid: proj(v._t) for vid, v in on.items()}
        edges_in = [(e.v1.id, e.v2.id) for e in self.edges.values() if e.v1.id in on and e.v2.id in on]
        old_faces = [f for f in self.faces.values() if all(v.id in on for v in f.loops[0])]
        regions = find_regions(pts, edges_in)
        if not regions and not old_faces:
            return result
        seeds = self._seeds(old_faces, requests, plane, proj)
        new_keys = {frozenset((e.v1.id, e.v2.id)) for e in new_edges if e.parent is self}
        decisions: list[tuple[Region, int, list[_Seed], list[_Seed]]] = []
        for region in regions:
            outer2d = [pts[v] for v in region.outer]
            holes2d = [[pts[v] for v in h] for h in region.holes]
            sample = interior_point(outer2d, holes2d)
            covering = [s for s in seeds if point_in_loops(sample, s.loops)]
            olds = [s for s in covering if s.face is not None]
            reqs = [s for s in covering if s.request is not None]
            if covering:
                cancelled = cancel_opposite and any(
                    r.cancels and any(o.sign != r.sign for o in olds) for r in reqs
                )
                if cancelled:
                    decisions.append((region, 0, olds, reqs))
                    continue
                main = max(olds, key=lambda s: s.area) if olds else reqs[-1]
                decisions.append((region, main.sign, olds, reqs))
            elif auto_faces and region.outer_edges() & new_keys:
                decisions.append((region, self._auto_sign(region, set(old_faces)), [], []))
        return self._apply_decisions(decisions, old_faces, requests, result)

    def _seeds(
        self,
        old_faces: list["Face"],
        requests: Sequence[FaceSpec],
        plane: Plane,
        proj: Callable[[Sequence[float]], Point2],
    ) -> list[_Seed]:
        """Build containment seeds from existing faces and face requests."""
        seeds: list[_Seed] = []
        n = plane.normal
        for f in old_faces:
            loops = [[proj(v._t) for v in loop] for loop in f.loops]
            sign = 1 if float(f.normal @ n) > 0 else -1
            seeds.append(_Seed(loops, sign, abs(signed_area(loops[0])), face=f))
        for i, spec in enumerate(requests):
            loops = [[proj(v3(p)) for p in loop] for loop in [spec.outer, *spec.holes]]
            normal = v3(spec.normal) if spec.normal is not None else newell_normal(
                [v3(p) for p in spec.outer]
            )
            sign = 1 if float(normal @ n) > 0 else -1
            seeds.append(
                _Seed(loops, sign, abs(signed_area(loops[0])), request=i, cancels=spec.cancels)
            )
        return seeds

    def _auto_sign(self, region: Region, plane_faces: set["Face"]) -> int:
        """Orientation for a brand-new face: agree with neighbouring faces, else up."""
        votes = 0
        loop = region.outer
        for i, vid in enumerate(loop):
            a, b = self.vertices[vid], self.vertices[loop[(i + 1) % len(loop)]]
            e = self.edge_between(a, b)
            if e is None:
                continue
            for g in e.faces:
                if g in plane_faces:
                    continue
                direction = g.uses_directed(a, b)
                if direction is True:
                    votes -= 1
                elif direction is False:
                    votes += 1
        return 1 if votes >= 0 else -1

    def _apply_decisions(
        self,
        decisions: list[tuple[Region, int, list[_Seed], list[_Seed]]],
        old_faces: list["Face"],
        requests: Sequence[FaceSpec],
        result: list[list["Face"]],
    ) -> list[list["Face"]]:
        """Replace a plane's old faces with the faces decided for its regions."""
        reuse: dict[int, Face] = {}
        for f in sorted(old_faces, key=lambda face: -face.area()):
            options = [
                i for i, (_, sign, olds, _) in enumerate(decisions)
                if sign != 0 and i not in reuse and any(s.face is f for s in olds)
            ]
            if options:
                reuse[max(options, key=lambda i: decisions[i][0].area)] = f
        reused = set(reuse.values())
        for f in old_faces:
            self._detach_face(f)
        successors: dict[Face, list[int]] = {f: [] for f in old_faces}
        for i, (region, sign, olds, reqs) in enumerate(decisions):
            if sign == 0:
                continue
            loops = [[self.vertices[v] for v in region.outer]]
            loops += [[self.vertices[v] for v in hole] for hole in region.holes]
            if sign < 0:
                loops = [list(reversed(loop)) for loop in loops]
            face = reuse.get(i)
            if face is not None:
                face.loops = loops
                self._attach_face(face)
                self.registry.modified(face.id)
            else:
                template = max(olds, key=lambda s: s.area).face if olds else None
                face = self._create_face(loops, template)
                if template is None and reqs:
                    spec = requests[reqs[-1].request]  # type: ignore[index]
                    face.material = spec.material
                    face.back_material = spec.back_material
                    face.tag = spec.tag
            for s in olds:
                successors[s.face].append(face.id)  # type: ignore[index]
            for s in reqs:
                result[s.request].append(face)  # type: ignore[index]
        for f in old_faces:
            if f not in reused:
                self._discard_face(f)
            succ = successors[f]
            if succ and succ != [f.id]:
                self.registry.replaced(f.id, succ)
        return result

    # ------------------------------------------------------------------ erasing & healing
    def erase_faces(self, faces: Iterable["Face"]) -> None:
        """Erase faces, keeping their edges."""
        for f in list(faces):
            if f.parent is self:
                self._discard_face(f)

    def erase_edges(self, edges: Iterable["Edge"], heal: bool = True) -> None:
        """Erase edges like SketchUp's eraser.

        Faces bounded by an erased edge are erased too, except that erasing the edge
        between two coplanar, same-facing, same-material faces merges them (``heal``).
        """
        doomed_edges = [e for e in dict.fromkeys(edges) if e.parent is self]
        doomed_faces: dict[Face, None] = {}
        healing: list[Edge] = []
        for e in doomed_edges:
            fs = list(e.faces)
            if heal and len(fs) == 2 and _mergeable(fs[0], fs[1]):
                healing.append(e)
            else:
                doomed_faces.update(dict.fromkeys(fs))
        planes: dict[tuple[float, ...], Plane] = {}
        for e in healing:
            if any(f in doomed_faces for f in e.faces):
                doomed_faces.update(dict.fromkeys(e.faces))
                continue
            pl = next(iter(e.faces)).plane
            planes.setdefault(plane_key(pl), pl)
        for f in doomed_faces:
            if f.parent is self:
                self._discard_face(f)
        touched = [v for e in doomed_edges for v in (e.v1, e.v2)]
        for e in doomed_edges:
            self._discard_edge(e)
        for pl in planes.values():
            self.resolve_plane(pl)
        self.remove_orphan_vertices(touched)
        self.heal_vertices(touched)

    def erase(self, entities: Iterable["Entity"]) -> None:
        """Erase any mix of faces, edges, vertices and instances."""
        from pymodeler.core.components import ComponentInstance, remove_instance
        from pymodeler.core.entities import Edge, Face, Vertex

        faces, edges = [], []
        for ent in entities:
            if isinstance(ent, Face):
                faces.append(ent)
            elif isinstance(ent, Edge):
                edges.append(ent)
            elif isinstance(ent, Vertex):
                edges.extend(ent.edges)
            elif isinstance(ent, ComponentInstance):
                remove_instance(self, ent)  # type: ignore[arg-type]
        self.erase_faces(faces)
        self.erase_edges(edges)

    def merge_coplanar(self, edges: Iterable["Edge"]) -> int:
        """Remove edges that separate coplanar, same-facing, same-material faces.

        Returns:
            Number of edges removed.
        """
        doomed = [
            e for e in dict.fromkeys(edges)
            if e.parent is self and len(e.faces) == 2 and _mergeable(*e.faces)
        ]
        if not doomed:
            return 0
        planes: dict[tuple[float, ...], Plane] = {}
        for e in doomed:
            pl = next(iter(e.faces)).plane
            planes.setdefault(plane_key(pl), pl)
        touched = [v for e in doomed for v in (e.v1, e.v2)]
        for e in doomed:
            self._discard_edge(e)
        for pl in planes.values():
            self.resolve_plane(pl)
        self.remove_orphan_vertices(touched)
        self.heal_vertices(touched)
        return len(doomed)

    def remove_stray_edges(self, edges: Iterable["Edge"]) -> int:
        """Erase the given edges that no longer bound any face.

        Returns:
            Number of edges removed.
        """
        doomed = [e for e in dict.fromkeys(edges) if e.parent is self and not e.faces]
        touched = [v for e in doomed for v in (e.v1, e.v2)]
        for e in doomed:
            self._discard_edge(e)
        self.remove_orphan_vertices(touched)
        self.heal_vertices(touched)
        return len(doomed)

    def heal_vertices(self, vertices: Iterable["Vertex"]) -> int:
        """Remove vertices that only join two collinear, equivalent edges.

        Returns:
            Number of vertices removed.
        """
        count = 0
        for v in dict.fromkeys(vertices):
            if v.parent is not self or len(v.edges) != 2:
                continue
            e1, e2 = list(v.edges)
            u, w = e1.other(v), e2.other(v)
            if u is w or not point_on_segment_interior(v._t, u._t, w._t):
                continue
            if set(e1.faces) != set(e2.faces) or _edge_style(e1) != _edge_style(e2):
                continue
            if self.edge_between(u, w) is not None:
                continue
            faces = list(e1.faces)
            self._discard_edge(e2)
            self._reconnect_edge(e1, u, w)
            for f in faces:
                for loop in f.loops:
                    if v in loop:
                        loop.remove(v)
                f.invalidate()
                e1.faces[f] = None
            self._discard_vertex(v)
            self.registry.replaced(e2.id, [e1.id])
            count += 1
        return count


# ---------------------------------------------------------------------- helpers


def _mergeable(f1: "Face", f2: "Face") -> bool:
    """True if two faces are coplanar, face the same way and look the same."""
    if f1 is f2 or f1.material != f2.material or f1.back_material != f2.back_material:
        return False
    if float(f1.normal @ f2.normal) < 1 - 1e-9:
        return False
    plane = f1.plane
    return all(abs(plane.distance(v._t)) <= TOL for v in f2.loops[0])


def _edge_style(e: "Edge") -> tuple[object, ...]:
    """Attributes that must match for two edges to be merged into one."""
    return (e.soft, e.smooth, e.hidden, e.curve, e.tag)


def _bbox(a: Sequence[float], b: Sequence[float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Bounding box of a segment, padded by the tolerance."""
    lo = tuple(min(a[i], b[i]) - TOL for i in range(3))
    hi = tuple(max(a[i], b[i]) + TOL for i in range(3))
    return lo, hi


def _bbox_overlap(
    lo: Sequence[float], hi: Sequence[float], p: Sequence[float], q: Sequence[float]
) -> bool:
    """True if the box ``lo..hi`` overlaps the bounding box of segment ``pq``."""
    for i in range(3):
        if max(p[i], q[i]) < lo[i] or min(p[i], q[i]) > hi[i]:
            return False
    return True


def _point_in_bbox(p: Sequence[float], lo: Sequence[float], hi: Sequence[float]) -> bool:
    """True if ``p`` lies within the box ``lo..hi``."""
    return all(lo[i] <= p[i] <= hi[i] for i in range(3))

