"""Tests for the sticky geometry kernel: faces from loops, splitting, healing, holes."""

import itertools

import numpy as np
import pytest

from pymodeler.core.analysis import is_closed_manifold
from pymodeler.core.entities import Entities
from pymodeler.core.vec import GeometryError
from pymodeler.ops.extrude import push_pull

SQUARE = [(0, 0, 0), (1000, 0, 0), (1000, 1000, 0), (0, 1000, 0)]


def square_segments(pts=SQUARE):
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def areas(ents: Entities) -> list[float]:
    return sorted(round(f.area(), 3) for f in ents.faces.values())


def edge_at(ents: Entities, a, b):
    va, vb = ents.find_vertex(a), ents.find_vertex(b)
    assert va is not None and vb is not None
    return ents.edge_between(va, vb)


# ----------------------------------------------------------------- faces from loops


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_closed_loop_makes_face_in_any_order(order) -> None:
    ents = Entities()
    segs = square_segments()
    for i in order:
        ents.add_line(*segs[i])
    assert len(ents.faces) == 1
    face = next(iter(ents.faces.values()))
    assert face.area() == pytest.approx(1_000_000)
    assert np.allclose(face.normal, (0, 0, 1)), "horizontal faces face up by default"
    assert len(ents.edges) == 4 and len(ents.vertices) == 4


def test_open_or_non_planar_loops_make_no_face() -> None:
    ents = Entities()
    ents.add_polyline(SQUARE[:3])
    assert not ents.faces
    skew = Entities()
    skew.add_polyline([(0, 0, 0), (1000, 0, 0), (1000, 1000, 300), (0, 1000, 0)], closed=True)
    assert not skew.faces


def test_vertical_faces_face_front_by_default() -> None:
    ents = Entities()
    ents.add_polyline([(0, 0, 0), (1000, 0, 0), (1000, 0, 1000), (0, 0, 1000)], closed=True)
    face = next(iter(ents.faces.values()))
    assert np.allclose(face.normal, (0, -1, 0))


def test_vertices_merge_within_tolerance() -> None:
    ents = Entities()
    ents.add_line((0, 0, 0), (1000, 0, 0))
    ents.add_line((1000.0004, 0, 0), (1000, 1000, 0))
    assert len(ents.vertices) == 3


def test_add_face_with_hole() -> None:
    ents = Entities()
    hole = [(300, 300, 0), (700, 300, 0), (700, 700, 0), (300, 700, 0)]
    faces = ents.add_face(SQUARE, [hole], material="red")
    assert len(faces) == 1
    face = faces[0]
    assert len(face.loops) == 2
    assert face.area() == pytest.approx(1_000_000 - 160_000)
    assert face.material == "red"
    assert len(ents.faces) == 1, "an explicit hole is not filled"


def test_add_face_rejects_bad_input() -> None:
    ents = Entities()
    with pytest.raises(GeometryError):
        ents.add_face([(0, 0, 0), (1, 0, 0)])
    with pytest.raises(GeometryError):
        ents.add_face([(0, 0, 0), (1000, 0, 0), (1000, 1000, 500), (0, 1000, 0)])


# ----------------------------------------------------------------- edge splitting


def test_crossing_edges_split_each_other() -> None:
    ents = Entities()
    ents.add_line((0, 0, 0), (1000, 1000, 0))
    ents.add_line((0, 1000, 0), (1000, 0, 0))
    assert len(ents.edges) == 4
    assert ents.find_vertex((500, 500, 0)) is not None


def test_t_junction_splits_existing_edge() -> None:
    ents = Entities()
    ents.add_line((0, 0, 0), (1000, 0, 0))
    ents.add_line((400, 0, 0), (400, 800, 0))
    assert sorted(round(e.length()) for e in ents.edges.values()) == [400, 600, 800]


def test_collinear_overlap_does_not_duplicate() -> None:
    ents = Entities()
    ents.add_line((0, 0, 0), (1000, 0, 0))
    ents.add_line((500, 0, 0), (1500, 0, 0))
    assert sorted(round(e.length()) for e in ents.edges.values()) == [500, 500, 500]
    ents.add_line((-500, 0, 0), (2000, 0, 0))
    assert sorted(round(e.length()) for e in ents.edges.values()) == [500] * 5


def test_skew_lines_do_not_split() -> None:
    ents = Entities()
    ents.add_line((0, 0, 0), (1000, 0, 0))
    ents.add_line((500, -500, 100), (500, 500, 100))
    assert len(ents.edges) == 2


def test_split_edge_updates_face_loops() -> None:
    ents = Entities()
    face = ents.add_face(SQUARE)[0]
    e = edge_at(ents, (0, 0, 0), (1000, 0, 0))
    v = ents.add_vertex((500, 0, 0))
    ents.split_edge(e, v)
    assert len(face.outer_loop) == 5
    assert len(face.edges()) == 5
    assert face.area() == pytest.approx(1_000_000)


# ----------------------------------------------------------------- faces split by edges


def test_line_across_face_splits_it_and_keeps_material() -> None:
    ents = Entities()
    face = ents.add_face(SQUARE, material="brick")[0]
    with ents.registry.tracking() as log:
        ents.add_line((500, 0, 0), (500, 1000, 0))
    assert areas(ents) == [500_000, 500_000]
    assert all(f.material == "brick" for f in ents.faces.values())
    assert face.alive, "the original face keeps its identity as one of the halves"
    assert len(log.successors(face.id, ents.registry)) == 2


def test_inner_loop_makes_hole_and_inner_face() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.add_polyline([(300, 300, 0), (700, 300, 0), (700, 700, 0), (300, 700, 0)], closed=True)
    assert areas(ents) == [160_000, 840_000]
    ring = max(ents.faces.values(), key=lambda f: f.area())
    assert len(ring.loops) == 2


def test_rectangle_touching_edge_makes_u_shape() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.add_polyline([(300, 0, 0), (700, 0, 0), (700, 600, 0), (300, 600, 0)], closed=True)
    assert areas(ents) == [240_000, 760_000]
    assert all(len(f.loops) == 1 for f in ents.faces.values())


def test_dangling_line_and_bridge_leave_faces_alone() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.add_line((200, 200, 0), (400, 400, 0))
    assert areas(ents) == [1_000_000]
    ents.add_line((0, 500, 0), (300, 500, 0))  # touches the boundary, still dangling
    assert areas(ents) == [1_000_000]
    ents.add_polyline([(600, 600, 0), (800, 600, 0), (800, 800, 0), (600, 800, 0)], closed=True)
    ents.add_line((1000, 700, 0), (800, 700, 0))  # bridge from outer loop to inner loop
    assert areas(ents) == [40_000, 960_000]


def test_two_loops_sharing_an_edge() -> None:
    ents = Entities()
    ents.add_polyline(SQUARE, closed=True)
    ents.add_polyline([(1000, 0, 0), (2000, 0, 0), (2000, 1000, 0), (1000, 1000, 0)])
    assert areas(ents) == [1_000_000, 1_000_000]


# ----------------------------------------------------------------- erasing and healing


def test_erasing_split_line_heals_face_and_edges() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.add_line((500, 0, 0), (500, 1000, 0))
    assert len(ents.edges) == 7
    ents.erase_edges([edge_at(ents, (500, 0, 0), (500, 1000, 0))])
    assert areas(ents) == [1_000_000]
    assert len(ents.edges) == 4 and len(ents.vertices) == 4


def test_erasing_boundary_edge_erases_face() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.erase_edges([edge_at(ents, (0, 0, 0), (1000, 0, 0))])
    assert not ents.faces
    assert len(ents.edges) == 3


def test_erasing_edge_between_differently_painted_faces_erases_both() -> None:
    ents = Entities()
    ents.add_face(SQUARE)
    ents.add_line((500, 0, 0), (500, 1000, 0))
    left = min(ents.faces.values(), key=lambda f: f.centroid()[0])
    left.material = "red"
    ents.erase_edges([edge_at(ents, (500, 0, 0), (500, 1000, 0))])
    assert not ents.faces


def test_erase_faces_keeps_edges_and_redrawing_restores_outward_face() -> None:
    ents = Entities()
    push_pull(ents, ents.add_face(SQUARE)[0], 1000)
    top = max(ents.faces.values(), key=lambda f: f.normal[2])
    ents.erase_faces([top])
    assert len(ents.faces) == 5 and len(ents.edges) == 12
    ents.add_line((0, 0, 1000), (1000, 1000, 1000))  # diagonal re-creates two triangles
    assert len(ents.faces) == 7
    assert is_closed_manifold(ents), "new faces must be oriented consistently with the box"


def test_moving_a_vertex_invalidates_cached_normal() -> None:
    ents = Entities()
    face = ents.add_face([(0, 0, 0), (1000, 0, 0), (0, 1000, 0)])[0]
    assert np.allclose(face.normal, (0, 0, 1))
    ents.find_vertex((0, 1000, 0)).position = (0, 0, 1000)
    assert np.allclose(face.normal, (0, -1, 0))
    assert ents.find_vertex((0, 0, 1000)) is not None, "the spatial index follows the move"
