"""Tests for 2D predicates, triangulation and region finding."""

import math
import random

import pytest

from pymodeler.core.planar import (
    find_regions,
    interior_point,
    point_in_loops,
    point_in_polygon,
    polygon_centroid,
    signed_area,
    triangulate,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
HOLE = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]


def tri_area(pts, tris) -> float:
    total = 0.0
    for a, b, c in tris:
        (x1, y1), (x2, y2), (x3, y3) = pts[a], pts[b], pts[c]
        area = ((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)) / 2
        assert area > 0, "triangles must be counter-clockwise"
        total += area
    return total


def circle(cx: float, cy: float, r: float, n: int, cw: bool = False):
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts[::-1] if cw else pts


def test_signed_area_and_point_in_polygon() -> None:
    assert signed_area(SQUARE) == 100.0
    assert signed_area(SQUARE[::-1]) == -100.0
    assert point_in_polygon((5, 5), SQUARE)
    assert not point_in_polygon((15, 5), SQUARE)
    assert point_in_loops((1, 1), [SQUARE, HOLE])
    assert not point_in_loops((5, 5), [SQUARE, HOLE])


def test_centroid() -> None:
    assert polygon_centroid(SQUARE) == pytest.approx((5.0, 5.0))
    assert polygon_centroid(SQUARE, [HOLE]) == pytest.approx((5.0, 5.0))


def test_triangulate_square_and_reversed() -> None:
    for outer in (SQUARE, SQUARE[::-1]):
        tris = triangulate(outer)
        assert len(tris) == 2
        assert tri_area(outer, tris) == pytest.approx(100.0)


def test_triangulate_concave() -> None:
    l_shape = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]
    tris = triangulate(l_shape)
    assert len(tris) == 4
    assert tri_area(l_shape, tris) == pytest.approx(64.0)


def test_triangulate_with_holes() -> None:
    pts = SQUARE + HOLE
    tris = triangulate(SQUARE, [HOLE])
    assert tri_area(pts, tris) == pytest.approx(96.0)
    # Two holes, and a circle with a circular hole.
    hole2 = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]
    tris = triangulate(SQUARE, [HOLE, hole2])
    assert tri_area(SQUARE + HOLE + hole2, tris) == pytest.approx(95.0)
    outer, inner = circle(0, 0, 10, 48), circle(0, 0, 5, 48, cw=True)
    tris = triangulate(outer, [inner])
    expected = abs(signed_area(outer)) - abs(signed_area(inner))
    assert tri_area(outer + inner, tris) == pytest.approx(expected)


def test_triangulate_hole_touching_nothing_random_convex() -> None:
    rng = random.Random(3)
    for _ in range(20):
        n = rng.randint(3, 40)
        outer = circle(rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(5, 20), n)
        tris = triangulate(outer)
        assert len(tris) == n - 2
        assert tri_area(outer, tris) == pytest.approx(abs(signed_area(outer)))


def test_interior_point() -> None:
    p = interior_point(SQUARE, [HOLE])
    assert point_in_loops(p, [SQUARE, HOLE])
    u_shape = [(0, 0), (10, 0), (10, 10), (8, 10), (8, 2), (2, 2), (2, 10), (0, 10)]
    assert point_in_polygon(interior_point(u_shape), u_shape)


def _grid_points(coords):
    return {name: xy for name, xy in coords.items()}


def test_find_regions_single_square() -> None:
    pts = {1: (0, 0), 2: (10, 0), 3: (10, 10), 4: (0, 10)}
    regions = find_regions(pts, [(1, 2), (2, 3), (3, 4), (4, 1)])
    assert len(regions) == 1
    assert regions[0].area == pytest.approx(100.0)
    assert set(regions[0].outer) == {1, 2, 3, 4}
    assert signed_area([pts[v] for v in regions[0].outer]) > 0


def test_find_regions_split_square() -> None:
    pts = {1: (0, 0), 2: (5, 0), 3: (10, 0), 4: (10, 10), 5: (5, 10), 6: (0, 10)}
    edges = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1), (2, 5)]
    regions = find_regions(pts, edges)
    assert sorted(r.area for r in regions) == pytest.approx([50.0, 50.0])


def test_find_regions_hole_nesting_and_island() -> None:
    pts = {1: (0, 0), 2: (10, 0), 3: (10, 10), 4: (0, 10),
           5: (4, 4), 6: (6, 4), 7: (6, 6), 8: (4, 6)}
    edges = [(1, 2), (2, 3), (3, 4), (4, 1), (5, 6), (6, 7), (7, 8), (8, 5)]
    regions = find_regions(pts, edges)
    assert len(regions) == 2
    big, small = regions
    assert big.area == pytest.approx(100.0) and len(big.holes) == 1
    assert set(big.holes[0]) == {5, 6, 7, 8}
    assert signed_area([pts[v] for v in big.holes[0]]) < 0
    assert small.area == pytest.approx(4.0) and not small.holes


def test_find_regions_ignores_filaments_and_bridges() -> None:
    pts = {1: (0, 0), 2: (10, 0), 3: (10, 10), 4: (0, 10),
           5: (4, 4), 6: (6, 4), 7: (6, 6), 8: (4, 6), 9: (20, 20)}
    edges = [(1, 2), (2, 3), (3, 4), (4, 1), (5, 6), (6, 7), (7, 8), (8, 5),
             (3, 9),          # dangling edge going outside
             (1, 5)]          # bridge from the outer loop to the inner loop
    regions = find_regions(pts, edges)
    assert len(regions) == 2
    assert len(regions[0].holes) == 1


def test_find_regions_two_squares_sharing_a_corner() -> None:
    pts = {1: (0, 0), 2: (1, 0), 3: (1, 1), 4: (0, 1), 5: (2, 1), 6: (2, 2), 7: (1, 2)}
    edges = [(1, 2), (2, 3), (3, 4), (4, 1), (3, 5), (5, 6), (6, 7), (7, 3)]
    regions = find_regions(pts, edges)
    assert sorted(r.area for r in regions) == pytest.approx([1.0, 1.0])


def test_find_regions_open_polyline_has_no_region() -> None:
    pts = {1: (0, 0), 2: (1, 0), 3: (1, 1)}
    assert find_regions(pts, [(1, 2), (2, 3)]) == []


def _star_polygon(rng: random.Random, n: int):
    """A random star-shaped polygon whose edges stay at least 2.5 from the origin."""
    while True:
        angs = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
        gaps = [(angs[(i + 1) % n] - angs[i]) % (2 * math.pi) for i in range(n)]
        if max(gaps) < 2 * math.pi / 3:
            break
    out = []
    for a in angs:
        r = rng.uniform(5, 20)
        out.append((math.cos(a) * r, math.sin(a) * r))
    return out


def test_triangulate_fuzz_star_polygons_with_holes() -> None:
    rng = random.Random(7)
    for _ in range(300):
        outer = _star_polygon(rng, rng.randint(4, 40))
        holes = []
        if rng.random() < 0.5:
            m = rng.randint(3, 16)
            holes.append(circle(0, 0, rng.uniform(0.5, 2.0), m, cw=True))
        pts = outer + [p for h in holes for p in h]
        tris = triangulate(outer, holes)
        expected = abs(signed_area(outer)) - sum(abs(signed_area(h)) for h in holes)
        assert tri_area(pts, tris) == pytest.approx(expected, rel=1e-9)
