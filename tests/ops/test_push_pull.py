"""Tests for push/pull: solids, extension, shortening, pockets, holes and openings."""

import math

import numpy as np
import pytest

from pymodeler.core.analysis import euler_characteristic, is_closed_manifold, signed_volume
from pymodeler.core.entities import Entities, Face
from pymodeler.core.vec import GeometryError
from pymodeler.ops.extrude import push_pull


def rect(ents: Entities, x0, y0, x1, y1, z=0.0, **kw) -> Face:
    faces = ents.add_face([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], **kw)
    assert len(faces) == 1
    return faces[0]


def face_facing(ents: Entities, normal) -> Face:
    n = np.array(normal, dtype=float)
    faces = [f for f in ents.faces.values() if np.allclose(f.normal, n)]
    return max(faces, key=lambda f: float(f.centroid() @ n))


def counts(ents: Entities) -> tuple[int, int, int]:
    return len(ents.vertices), len(ents.edges), len(ents.faces)


def box(height=500.0) -> Entities:
    ents = Entities()
    push_pull(ents, rect(ents, 0, 0, 1000, 1000), height)
    return ents


def assert_solid(ents: Entities, volume_mm3: float, genus: int = 0) -> None:
    assert is_closed_manifold(ents)
    assert signed_volume(ents) == pytest.approx(volume_mm3)
    assert euler_characteristic(ents) == 2 - 2 * genus


def test_push_free_rectangle_up_makes_box() -> None:
    ents = Entities()
    result = push_pull(ents, rect(ents, 0, 0, 1000, 1000, material="oak"), 500)
    assert counts(ents) == (8, 12, 6)
    assert_solid(ents, 1000 * 1000 * 500)
    assert len(result.caps) == 1 and len(result.sides) == 4
    assert np.allclose(result.caps[0].normal, (0, 0, 1))
    assert np.allclose(face_facing(ents, (0, 0, -1)).centroid(), (500, 500, 0))
    assert result.caps[0].material == "oak"


def test_push_free_rectangle_down_extrudes_below() -> None:
    ents = Entities()
    push_pull(ents, rect(ents, 0, 0, 1000, 1000), -500)
    assert counts(ents) == (8, 12, 6)
    assert_solid(ents, 1000 * 1000 * 500)
    zs = [v.position[2] for v in ents.vertices.values()]
    assert min(zs) == pytest.approx(-500) and max(zs) == pytest.approx(0)


def test_push_top_up_extends_box_without_extra_edges() -> None:
    ents = box()
    push_pull(ents, face_facing(ents, (0, 0, 1)), 300)
    assert counts(ents) == (8, 12, 6)
    assert_solid(ents, 1000 * 1000 * 800)


def test_push_top_down_shortens_box() -> None:
    ents = box()
    push_pull(ents, face_facing(ents, (0, 0, 1)), -200)
    assert counts(ents) == (8, 12, 6)
    assert_solid(ents, 1000 * 1000 * 300)


def test_push_side_face_out() -> None:
    ents = box()
    push_pull(ents, face_facing(ents, (1, 0, 0)), 250)
    assert counts(ents) == (8, 12, 6)
    assert_solid(ents, 1250 * 1000 * 500)


def test_pocket_in_top_face() -> None:
    ents = box()
    inner = rect(ents, 300, 300, 700, 700, z=500)
    push_pull(ents, inner, -200)
    assert counts(ents) == (16, 24, 11)
    assert_solid(ents, 1000 * 1000 * 500 - 400 * 400 * 200)


def test_raised_boss_on_top_face() -> None:
    ents = box()
    inner = rect(ents, 300, 300, 700, 700, z=500)
    push_pull(ents, inner, 200)
    assert_solid(ents, 1000 * 1000 * 500 + 400 * 400 * 200)


def test_push_through_makes_hole() -> None:
    ents = box()
    inner = rect(ents, 300, 300, 700, 700, z=500)
    result = push_pull(ents, inner, -500)
    assert not result.caps, "the cap cancels against the bottom face"
    assert counts(ents) == (16, 24, 10)
    assert_solid(ents, 1000 * 1000 * 500 - 400 * 400 * 500, genus=1)
    bottom = face_facing(ents, (0, 0, -1))
    assert len(bottom.loops) == 2


def test_door_opening_through_wall() -> None:
    ents = Entities()
    wall = ents.add_face([(0, 0, 0), (4000, 0, 0), (4000, 200, 0), (0, 200, 0)])[0]
    push_pull(ents, wall, 2400)
    door = ents.add_face([(1000, 0, 0), (1900, 0, 0), (1900, 0, 2100), (1000, 0, 2100)])
    assert len(door) == 1
    push_pull(ents, door[0], -200)
    assert_solid(ents, 4000 * 200 * 2400 - 900 * 200 * 2100)
    front = [f for f in ents.faces.values() if np.allclose(f.normal, (0, -1, 0))]
    assert len(front) == 1 and front[0].area() == pytest.approx(4000 * 2400 - 900 * 2100)
    assert len(face_facing(ents, (0, 0, -1)).loops) == 1, "the floor strip is split, not holed"


def test_window_opening_through_wall() -> None:
    ents = Entities()
    wall = ents.add_face([(0, 0, 0), (4000, 0, 0), (4000, 200, 0), (0, 200, 0)])[0]
    push_pull(ents, wall, 2400)
    window = ents.add_face([(2500, 0, 900), (3500, 0, 900), (3500, 0, 2000), (2500, 0, 2000)])[0]
    push_pull(ents, window, -200)
    assert_solid(ents, 4000 * 200 * 2400 - 1000 * 200 * 1100, genus=1)


def test_create_new_keeps_original_face() -> None:
    ents = box()
    top = face_facing(ents, (0, 0, 1))
    push_pull(ents, top, 300, create_new=True)
    assert top.alive
    assert len(ents.faces) == 11  # box (6) + four new sides + new top


def test_cylinder_side_edges_are_soft() -> None:
    ents = Entities()
    n = 24
    pts = [(500 * math.cos(2 * math.pi * i / n), 500 * math.sin(2 * math.pi * i / n), 0) for i in range(n)]
    face = ents.add_face(pts)[0]
    curve = ents.registry.new_id()
    for e in face.edges():
        e.curve = curve
    push_pull(ents, face, 1000)
    vertical = [e for e in ents.edges.values() if abs(e.direction()[2]) > 0.99]
    assert len(vertical) == n
    assert all(e.soft and e.smooth for e in vertical)
    top_edges = [e for e in ents.edges.values() if e.v1.position[2] > 999 and e.v2.position[2] > 999]
    assert len({e.curve for e in top_edges}) == 1 and top_edges[0].curve not in (None, curve)
    assert is_closed_manifold(ents)


def test_zero_distance_is_a_no_op_and_foreign_face_is_rejected() -> None:
    ents = Entities()
    face = rect(ents, 0, 0, 1000, 1000)
    assert not push_pull(ents, face, 0).faces
    assert len(ents.faces) == 1
    with pytest.raises(GeometryError):
        push_pull(Entities(), face, 100)
