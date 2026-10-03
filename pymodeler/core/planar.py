"""2D planar algorithms: polygon predicates, region finding and triangulation.

The sticky-geometry kernel projects every edge that lies in a plane onto 2D and asks
:func:`find_regions` for the bounded regions those edges enclose.  Regions are
returned as an outer loop (counter-clockwise) plus hole loops (clockwise) of vertex
ids.  :func:`triangulate` turns a polygon with holes into triangles; it is used for
rendering, export, area/centroid computations and for finding interior sample points.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Hashable, Iterable, Sequence

Point2 = tuple[float, float]
VertexId = Hashable


# --------------------------------------------------------------------------- predicates


def signed_area(poly: Sequence[Point2]) -> float:
    """Signed area of a polygon: positive when counter-clockwise."""
    total = 0.0
    count = len(poly)
    for i in range(count):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def cross(o: Point2, a: Point2, b: Point2) -> float:
    """Z component of ``(a - o) x (b - o)``; positive when ``o, a, b`` turn left."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def point_in_polygon(p: Point2, poly: Sequence[Point2]) -> bool:
    """Even-odd point-in-polygon test (points exactly on the boundary are ambiguous)."""
    x, y = p
    inside = False
    count = len(poly)
    j = count - 1
    for i in range(count):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_loops(p: Point2, loops: Sequence[Sequence[Point2]]) -> bool:
    """Even-odd test against several loops (an outer loop and its holes)."""
    inside = False
    for loop in loops:
        if point_in_polygon(p, loop):
            inside = not inside
    return inside


def distance_to_polygon_boundary(p: Point2, poly: Sequence[Point2]) -> float:
    """Shortest distance from ``p`` to any edge of ``poly``."""
    best = math.inf
    count = len(poly)
    for i in range(count):
        best = min(best, _point_segment_distance(p, poly[i], poly[(i + 1) % count]))
    return best


def _point_segment_distance(p: Point2, a: Point2, b: Point2) -> float:
    """Distance from ``p`` to segment ``ab``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def polygon_centroid(outer: Sequence[Point2], holes: Sequence[Sequence[Point2]] = ()) -> Point2:
    """Area centroid of a polygon with holes (falls back to the vertex average)."""
    total_a = 0.0
    cx = cy = 0.0
    for loop in [outer, *holes]:
        a = signed_area(loop)
        sign = 1.0 if loop is outer else -1.0
        a_abs = abs(a) * sign
        lx, ly = _loop_centroid(loop)
        total_a += a_abs
        cx += lx * a_abs
        cy += ly * a_abs
    if abs(total_a) < 1e-18:
        n = max(len(outer), 1)
        return (sum(p[0] for p in outer) / n, sum(p[1] for p in outer) / n)
    return (cx / total_a, cy / total_a)


def _loop_centroid(loop: Sequence[Point2]) -> Point2:
    """Centroid of a simple loop."""
    a = signed_area(loop)
    if abs(a) < 1e-18:
        n = max(len(loop), 1)
        return (sum(p[0] for p in loop) / n, sum(p[1] for p in loop) / n)
    cx = cy = 0.0
    count = len(loop)
    for i in range(count):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % count]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return (cx / (6.0 * a), cy / (6.0 * a))


# --------------------------------------------------------------------------- triangulation


def triangulate(
    outer: Sequence[Point2], holes: Sequence[Sequence[Point2]] = ()
) -> list[tuple[int, int, int]]:
    """Triangulate a polygon with holes by ear clipping.

    Args:
        outer: Outer boundary (any orientation).
        holes: Hole boundaries (any orientation), assumed inside ``outer`` and disjoint.

    Returns:
        Counter-clockwise triangles as index triples into ``outer + holes[0] + holes[1]...``.
    """
    pts: list[Point2] = list(outer)
    for hole in holes:
        pts.extend(hole)
    if len(outer) < 3:
        return []
    ring = _clean_ring(pts, list(range(len(outer))))
    if signed_area([pts[i] for i in ring]) < 0:
        ring.reverse()
    hole_rings: list[list[int]] = []
    offset = len(outer)
    for hole in holes:
        hr = _clean_ring(pts, list(range(offset, offset + len(hole))))
        offset += len(hole)
        if len(hr) < 3:
            continue
        if signed_area([pts[i] for i in hr]) > 0:
            hr.reverse()
        hole_rings.append(hr)
    if len(ring) < 3:
        return []
    ring = _merge_holes(pts, ring, hole_rings)
    return _ear_clip(pts, ring)


def _clean_ring(pts: Sequence[Point2], ring: list[int]) -> list[int]:
    """Drop consecutive duplicate points from a ring of indices."""
    out: list[int] = []
    for i in ring:
        if out and _same2(pts[out[-1]], pts[i]):
            continue
        out.append(i)
    while len(out) > 1 and _same2(pts[out[0]], pts[out[-1]]):
        out.pop()
    return out


def _same2(a: Point2, b: Point2, eps: float = 1e-12) -> bool:
    """Exact-ish equality of 2D points."""
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def _locally_inside(a: Point2, prev: Point2, nxt: Point2, target: Point2) -> bool:
    """Whether the direction ``a -> target`` points into the polygon interior at ``a``.

    The ring is oriented with the interior on the left of travel ``prev -> a -> nxt``.
    """
    e1 = (nxt[0] - a[0], nxt[1] - a[1])
    e2 = (prev[0] - a[0], prev[1] - a[1])
    d = (target[0] - a[0], target[1] - a[1])

    def cr(u: tuple[float, float], v: tuple[float, float]) -> float:
        return u[0] * v[1] - u[1] * v[0]

    if cross(prev, a, nxt) > 0:  # convex corner: interior wedge narrower than 180 degrees
        return cr(e1, d) > 0 and cr(d, e2) > 0
    exterior = cr(e2, d) >= 0 and cr(d, e1) >= 0
    return not exterior


def _segments_touch(a: Point2, b: Point2, c: Point2, d: Point2) -> bool:
    """True if closed segments ``ab`` and ``cd`` intersect (touching counts)."""
    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True

    def on_seg(p: Point2, q: Point2, r: Point2, val: float) -> bool:
        return val == 0 and min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(
            p[1], q[1]
        ) <= r[1] <= max(p[1], q[1])

    return (
        on_seg(c, d, a, d1)
        or on_seg(c, d, b, d2)
        or on_seg(a, b, c, d3)
        or on_seg(a, b, d, d4)
    )


def _bridge_ok(
    pts: Sequence[Point2],
    ring: list[int],
    k: int,
    hole: list[int],
    hi: int,
    other_holes: Sequence[list[int]],
) -> bool:
    """Check that the bridge from ``ring[k]`` to ``hole[hi]`` is a valid diagonal."""
    m, h = pts[ring[k]], pts[hole[hi]]
    if _same2(m, h):
        return True
    if not _locally_inside(m, pts[ring[k - 1]], pts[ring[(k + 1) % len(ring)]], h):
        return False
    if not _locally_inside(h, pts[hole[hi - 1]], pts[hole[(hi + 1) % len(hole)]], m):
        return False
    for loop in (ring, hole, *other_holes):
        count = len(loop)
        for i in range(count):
            p, q = pts[loop[i]], pts[loop[(i + 1) % count]]
            if _same2(p, m) or _same2(q, m) or _same2(p, h) or _same2(q, h):
                continue
            if _segments_touch(m, h, p, q):
                return False
    return True


def _merge_holes(pts: Sequence[Point2], ring: list[int], holes: list[list[int]]) -> list[int]:
    """Splice every hole into the outer ring through a visible bridge edge."""
    pending = sorted(holes, key=lambda hr: -max(pts[i][0] for i in hr))
    while pending:
        hole = pending.pop(0)
        hi = max(range(len(hole)), key=lambda j: (pts[hole[j]][0], pts[hole[j]][1]))
        h = pts[hole[hi]]
        order = sorted(
            range(len(ring)),
            key=lambda k: (pts[ring[k]][0] - h[0]) ** 2 + (pts[ring[k]][1] - h[1]) ** 2,
        )
        chosen = next((k for k in order if _bridge_ok(pts, ring, k, hole, hi, pending)), None)
        if chosen is None:
            chosen = order[0]
        rotated = hole[hi:] + hole[:hi]
        ring = ring[: chosen + 1] + rotated + [hole[hi], ring[chosen]] + ring[chosen + 1:]
    return ring


def _ear_clip(pts: Sequence[Point2], ring: list[int]) -> list[tuple[int, int, int]]:
    """Ear-clip a counter-clockwise (weakly simple) ring of point indices."""
    n = len(ring)
    if n < 3:
        return []
    xs = [pts[i][0] for i in ring]
    ys = [pts[i][1] for i in ring]
    scale = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    eps = 1e-12 * scale * scale
    prev = [(i - 1) % n for i in range(n)]
    nxt = [(i + 1) % n for i in range(n)]
    alive = n
    tris: list[tuple[int, int, int]] = []

    def pt(slot: int) -> Point2:
        return pts[ring[slot]]

    def area_at(slot: int) -> float:
        return cross(pt(prev[slot]), pt(slot), pt(nxt[slot]))

    def remove(slot: int) -> None:
        nxt[prev[slot]] = nxt[slot]
        prev[nxt[slot]] = prev[slot]

    def is_ear(slot: int) -> bool:
        a, b, c = pt(prev[slot]), pt(slot), pt(nxt[slot])
        if cross(a, b, c) <= eps:
            return False
        s = nxt[nxt[slot]]
        while s != prev[slot]:
            p = pt(s)
            if not (_same2(p, a) or _same2(p, b) or _same2(p, c)) and area_at(s) <= eps:
                if cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0:
                    return False
            s = nxt[s]
        return True

    slot = 0
    stall = 0
    while alive > 3:
        if is_ear(slot):
            tris.append((ring[prev[slot]], ring[slot], ring[nxt[slot]]))
            remove(slot)
            alive -= 1
            slot = nxt[slot]
            stall = 0
            continue
        slot = nxt[slot]
        stall += 1
        if stall < alive:
            continue
        # No ear in a full pass: drop a degenerate vertex, else force-clip the best one.
        candidate = slot
        best = -math.inf
        degenerate = None
        s = slot
        for _ in range(alive):
            a = area_at(s)
            if abs(a) <= eps:
                degenerate = s
                break
            if a > best:
                best, candidate = a, s
            s = nxt[s]
        if degenerate is not None:
            remove(degenerate)
            slot = nxt[degenerate]
        else:
            tris.append((ring[prev[candidate]], ring[candidate], ring[nxt[candidate]]))
            remove(candidate)
            slot = nxt[candidate]
        alive -= 1
        stall = 0
    if alive == 3 and area_at(slot) > eps:
        tris.append((ring[prev[slot]], ring[slot], ring[nxt[slot]]))
    return tris


def interior_point(outer: Sequence[Point2], holes: Sequence[Sequence[Point2]] = ()) -> Point2:
    """A point strictly inside a polygon with holes (centroid of its largest triangle)."""
    pts: list[Point2] = list(outer)
    for hole in holes:
        pts.extend(hole)
    best: Point2 | None = None
    best_area = -1.0
    for a, b, c in triangulate(outer, holes):
        area = abs(cross(pts[a], pts[b], pts[c]))
        if area > best_area:
            best_area = area
            best = (
                (pts[a][0] + pts[b][0] + pts[c][0]) / 3.0,
                (pts[a][1] + pts[b][1] + pts[c][1]) / 3.0,
            )
    if best is None:
        return polygon_centroid(outer, holes)
    return best


# --------------------------------------------------------------------------- regions


@dataclass
class Region:
    """A bounded region of a planar arrangement."""

    outer: list[VertexId]
    """Outer boundary, counter-clockwise, as vertex ids."""

    holes: list[list[VertexId]] = field(default_factory=list)
    """Hole boundaries, clockwise, as vertex ids."""

    area: float = 0.0
    """Area of the outer boundary (holes not subtracted)."""

    def boundary_edges(self) -> set[frozenset[VertexId]]:
        """Undirected vertex-id pairs of every edge on the region's boundary."""
        out: set[frozenset[VertexId]] = set()
        for loop in [self.outer, *self.holes]:
            for i, a in enumerate(loop):
                out.add(frozenset((a, loop[(i + 1) % len(loop)])))
        return out

    def outer_edges(self) -> set[frozenset[VertexId]]:
        """Undirected vertex-id pairs of the outer boundary only."""
        loop = self.outer
        return {frozenset((a, loop[(i + 1) % len(loop)])) for i, a in enumerate(loop)}


def find_regions(
    points: dict[VertexId, Point2], edges: Iterable[tuple[VertexId, VertexId]]
) -> list[Region]:
    """Find the bounded regions enclosed by a set of non-crossing 2D edges.

    Edges must already be split at every intersection (the 3D kernel guarantees this).
    Dangling edges and bridges (edges with the same region on both sides) do not bound
    regions and are ignored.  Components nested inside a region become its holes.

    Args:
        points: 2D coordinates per vertex id.
        edges: Undirected edges as vertex-id pairs.

    Returns:
        Regions sorted by decreasing area.
    """
    adj: dict[VertexId, set[VertexId]] = defaultdict(set)
    for a, b in edges:
        if a != b:
            adj[a].add(b)
            adj[b].add(a)
    while True:
        _prune_filaments(adj)
        cycles, owner = _trace_cycles(points, adj)
        bridges = [(a, b) for (a, b), c in owner.items() if owner[(b, a)] == c]
        if not bridges:
            break
        for a, b in bridges:
            adj[a].discard(b)
            adj[b].discard(a)
    components = _components(adj)
    comp_of = {v: i for i, comp in enumerate(components) for v in comp}
    bounded: list[tuple[list[VertexId], float, list[Point2]]] = []
    outers: dict[int, list[VertexId]] = {}
    outer_area: dict[int, float] = {}
    for cycle in cycles:
        poly = [points[v] for v in cycle]
        area = signed_area(poly)
        if area > 0:
            bounded.append((cycle, area, poly))
        else:
            c = comp_of[cycle[0]]
            if c not in outer_area or area < outer_area[c]:
                outers[c] = cycle
                outer_area[c] = area
    regions = [Region(outer=cyc, holes=[], area=area) for cyc, area, _ in bounded]
    for c, outer_cycle in outers.items():
        probe = points[outer_cycle[0]]
        parent = None
        parent_area = math.inf
        for idx, (cyc, area, poly) in enumerate(bounded):
            if comp_of[cyc[0]] == c or area >= parent_area:
                continue
            if point_in_polygon(probe, poly):
                parent, parent_area = idx, area
        if parent is not None:
            regions[parent].holes.append(outer_cycle)
    regions.sort(key=lambda r: -r.area)
    return regions


def _prune_filaments(adj: dict[VertexId, set[VertexId]]) -> None:
    """Repeatedly remove vertices of degree <= 1 (dangling edge chains)."""
    stack = [v for v, nb in adj.items() if len(nb) <= 1]
    while stack:
        v = stack.pop()
        nbrs = adj.get(v)
        if nbrs is None or len(nbrs) > 1:
            continue
        for w in nbrs:
            adj[w].discard(v)
            if len(adj[w]) <= 1:
                stack.append(w)
        del adj[v]


def _trace_cycles(
    points: dict[VertexId, Point2], adj: dict[VertexId, set[VertexId]]
) -> tuple[list[list[VertexId]], dict[tuple[VertexId, VertexId], int]]:
    """Trace the face cycles of a planar graph (interior on the left of travel)."""
    order: dict[VertexId, list[VertexId]] = {}
    index: dict[tuple[VertexId, VertexId], int] = {}
    for v, nbrs in adj.items():
        vx, vy = points[v]
        ordered = sorted(nbrs, key=lambda w: math.atan2(points[w][1] - vy, points[w][0] - vx))
        order[v] = ordered
        for i, w in enumerate(ordered):
            index[(v, w)] = i
    owner: dict[tuple[VertexId, VertexId], int] = {}
    cycles: list[list[VertexId]] = []
    for v in adj:
        for w in order[v]:
            if (v, w) in owner:
                continue
            cycle: list[VertexId] = []
            he = (v, w)
            while he not in owner:
                owner[he] = len(cycles)
                cycle.append(he[0])
                a, b = he
                around = order[b]
                he = (b, around[(index[(b, a)] - 1) % len(around)])
            cycles.append(cycle)
    return cycles, owner


def _components(adj: dict[VertexId, set[VertexId]]) -> list[list[VertexId]]:
    """Connected components of the graph (deterministic order)."""
    seen: set[VertexId] = set()
    comps: list[list[VertexId]] = []
    for start in adj:
        if start in seen:
            continue
        comp = []
        stack = [start]
        seen.add(start)
        while stack:
            v = stack.pop()
            comp.append(v)
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        comps.append(comp)
    return comps
