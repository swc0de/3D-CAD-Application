"""Tests for transforms, grouping, follow-me, offset, extrude, intersect, primitives, selectors."""

import math

import numpy as np
import pytest

from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.core.components import add_instance, context_world
from pymodeler.core.entities import Entities, Face
from pymodeler.core.materials import Material
from pymodeler.core.model import Model
from pymodeler.core.transform import rotation, scaling, translation
from pymodeler.core.vec import GeometryError
from pymodeler.ops import draw, primitives
from pymodeler.ops.edit import WorldFace, erase, intersect_faces
from pymodeler.ops.extrude import extrude, follow_me, offset_face, path_from_edges, push_pull
from pymodeler.ops.organize import explode, make_group, paint, set_tag
from pymodeler.ops.selectors import candidate_faces, select_faces
from pymodeler.ops.transform import bounds_of, copy_entities, linear_array, transform_entities


def all_of(ents: Entities):
    return [*ents.faces.values(), *ents.edges.values()]


def box_in(ents: Entities, origin=(0, 0, 0), size=(1000, 1000, 1000)):
    primitives.box(ents, origin, size)
    return all_of(ents)


# ----------------------------------------------------------------- transforms


def test_move_rotate_scale_raw_geometry() -> None:
    ents = Entities()
    items = box_in(ents)
    transform_entities(ents, items, translation((500, 0, 0)))
    lo, hi = bounds_of(items)
    assert np.allclose(lo, (500, 0, 0)) and np.allclose(hi, (1500, 1000, 1000))
    transform_entities(ents, items, rotation((0, 0, 1), 90, center=(500, 0, 0)))
    lo, hi = bounds_of(items)
    assert np.allclose(lo, (-500, 0, 0)) and np.allclose(hi, (500, 1000, 1000))
    transform_entities(ents, items, scaling(2, center=lo))
    assert signed_volume(ents) == pytest.approx(8e9)
    assert is_closed_manifold(ents)


def test_mirror_keeps_normals_outward() -> None:
    ents = Entities()
    items = box_in(ents)
    transform_entities(ents, items, scaling((-1, 1, 1)))
    assert signed_volume(ents) == pytest.approx(1e9)
    assert is_closed_manifold(ents)


def test_copy_and_array_raw_geometry() -> None:
    ents = Entities()
    items = box_in(ents)
    copied = copy_entities(ents, items, translation((2000, 0, 0)))
    assert len(copied.faces) == 6
    arrays = linear_array(ents, items, np.array([0, 2000, 0.0]), 3)
    assert len(arrays) == 3 and len(ents.faces) == 6 * 5
    assert signed_volume(ents) == pytest.approx(5e9)


def test_copy_preserves_curves_and_soft_edges() -> None:
    ents = Entities()
    primitives.cylinder(ents, (0, 0, 0), 300, 500, 24)
    copied = copy_entities(ents, all_of(ents), translation((1000, 0, 0)))
    soft = [e for e in copied.edges if e.soft]
    assert len(soft) == 24
    curves = {e.curve for e in copied.edges if e.curve is not None}
    original = {e.curve for e in ents.edges.values() if e.curve is not None} - curves
    assert len(curves) == 2 and not curves & original


# ----------------------------------------------------------------- groups & components


def test_make_group_moves_geometry_and_remaps_references() -> None:
    model = Model()
    items = box_in(model.entities)
    ids = [e.id for e in items]
    with model.registry.tracking() as log:
        group = make_group(model, model.entities, items, name="Room")
    assert not model.entities.faces and not model.entities.edges
    assert group.is_group and group.name == "Room"
    assert len(group.definition.entities.faces) == 6
    assert log.successors(ids[0], model.registry) == [group.id]
    lo, hi = model.bounds()
    assert np.allclose(lo, 0) and np.allclose(hi, 1000)


def test_make_component_sets_origin_at_min_corner() -> None:
    model = Model()
    items = box_in(model.entities, origin=(500, 500, 0))
    comp = make_group(model, model.entities, items, name="Block", component=True)
    assert not comp.is_group
    lo, _ = comp.definition.local_bounds()
    assert np.allclose(lo, 0)
    assert np.allclose(comp.transform[:3, 3], (500, 500, 0))


def test_explode_restores_geometry_with_inherited_material() -> None:
    model = Model()
    model.add_material(Material("red"))
    group = make_group(model, model.entities, box_in(model.entities))
    group.transform = translation((0, 0, 100))
    paint(model, [group], "red")
    created = explode(model, model.entities, group)
    assert len(model.entities.faces) == 6 and created
    assert all(f.material == "red" for f in model.entities.faces.values())
    assert min(v.position[2] for v in model.entities.vertices.values()) == pytest.approx(100)
    assert not model.definitions, "an exploded group's definition is removed"


def test_paint_and_tag() -> None:
    model = Model()
    model.add_material(Material("oak"))
    faces = primitives.box(model.entities, (0, 0, 0), (100, 100, 100))
    assert paint(model, faces, "oak", side="both") == 6
    assert all(f.material == "oak" and f.back_material == "oak" for f in faces)
    with pytest.raises(GeometryError):
        paint(model, faces, "nope")
    set_tag(model, faces, "Furniture")
    assert "Furniture" in model.tags and faces[0].tag == "Furniture"


def test_context_world_follows_group_transform() -> None:
    model = Model()
    group = make_group(model, model.entities, box_in(model.entities))
    group.transform = translation((0, 0, 500))
    assert np.allclose(context_world(group.definition.entities)[:3, 3], (0, 0, 500))
    assert np.allclose(context_world(model.entities), np.eye(4))


# ----------------------------------------------------------------- follow me / offset / extrude


def test_follow_me_open_path_square_tube() -> None:
    ents = Entities()
    profile = draw.rectangle(ents, (0, -50, -50), 100, 100, draw.plane_axes("yz")).faces[0]
    path = [np.array(p, dtype=float) for p in [(0, 0, 0), (1000, 0, 0), (1000, 1000, 0)]]
    follow_me(ents, profile, path)
    assert is_closed_manifold(ents)
    assert signed_volume(ents) == pytest.approx((1050 + 950) * 100 * 100)


def test_follow_me_closed_path_lathe_is_smooth() -> None:
    ents = Entities()
    profile = draw.circle(ents, (300, 0, 0), 100, 12, draw.plane_axes("xz")).faces[0]
    n = 16
    path = [np.array((300 * math.cos(2 * math.pi * k / n), 300 * math.sin(2 * math.pi * k / n), 0)) for k in range(n)]
    follow_me(ents, profile, path + [path[0]])
    assert is_closed_manifold(ents)
    assert len(ents.faces) == 12 * 16
    assert all(e.soft for e in ents.edges.values())


def test_path_from_edges_orders_chain() -> None:
    ents = Entities()
    edges = ents.add_polyline([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 50)])
    pts = path_from_edges(list(reversed(edges)))
    assert len(pts) == 4
    assert {tuple(np.round(pts[0])), tuple(np.round(pts[-1]))} == {(0, 0, 0), (0, 100, 50)}


def test_offset_in_and_out() -> None:
    ents = Entities()
    face = draw.rectangle(ents, (0, 0, 0), 1000, 1000).faces[0]
    inner = offset_face(ents, face, 100)
    assert [round(f.area()) for f in inner] == [640_000]
    assert sorted(round(f.area()) for f in ents.faces.values()) == [360_000, 640_000]
    ents2 = Entities()
    face2 = draw.rectangle(ents2, (0, 0, 0), 1000, 1000).faces[0]
    offset_face(ents2, face2, -100)
    assert sorted(round(f.area()) for f in ents2.faces.values()) == [440_000, 1_000_000]
    with pytest.raises(GeometryError):
        offset_face(Entities(), draw.rectangle(Entities(), (0, 0, 0), 10, 10).faces[0], 1)


def test_extrude_profile() -> None:
    ents = Entities()
    pts = [np.array(p, dtype=float) for p in [(0, 0, 0), (4000, 0, 0), (4000, 0, 1000), (2000, 0, 2000), (0, 0, 1000)]]
    faces = extrude(ents, pts, np.array([0, 3000, 0.0]))
    assert len(faces) == 7 and is_closed_manifold(ents)
    assert signed_volume(ents) == pytest.approx((4000 * 1000 + 0.5 * 4000 * 1000) * 3000)
    with pytest.raises(GeometryError):
        extrude(Entities(), pts, np.array([1.0, 0, 0]))


# ----------------------------------------------------------------- intersect & erase


def test_intersect_two_groups_adds_edges_to_both() -> None:
    model = Model()
    a = make_group(model, model.entities, box_in(model.entities))
    primitives.box(model.entities, (500, 500, 500), (1000, 1000, 1000))
    b = make_group(model, model.entities, all_of(model.entities))
    fa = [WorldFace(f, a.transform) for f in a.definition.entities.faces.values()]
    fb = [WorldFace(f, b.transform) for f in b.definition.entities.faces.values()]
    created = intersect_faces(fa, fb)
    assert created
    assert len(a.definition.entities.faces) == 6 + 3, "three faces of A are each split in two"
    assert len(b.definition.entities.faces) == 6 + 3


def test_erase_removes_targets() -> None:
    ents = Entities()
    items = box_in(ents)
    erase(ents, items)
    assert not ents.faces and not ents.edges and not ents.vertices


# ----------------------------------------------------------------- primitives


@pytest.mark.parametrize(
    "build, volume",
    [
        (lambda e: primitives.box(e, (0, 0, 0), (1000, 500, 300)), 1000 * 500 * 300),
        (lambda e: primitives.cylinder(e, (0, 0, 0), 500, 1000, 48), 0.5 * 48 * 500**2 * math.sin(2 * math.pi / 48) * 1000),
        (lambda e: primitives.cone(e, (0, 0, 0), 500, 1000, 4), 0.5 * 1000 * 1000 / 3 * 1000 / 1),
        (lambda e: primitives.sphere(e, (0, 0, 0), 500, 16, 8), None),
    ],
)
def test_primitives_are_closed_solids(build, volume) -> None:
    ents = Entities()
    build(ents)
    assert is_closed_manifold(ents)
    if volume is not None:
        assert signed_volume(ents) == pytest.approx(volume)
    else:
        assert 0.4e9 < signed_volume(ents) < 4 / 3 * math.pi * 500**3


def test_frustum_and_axis_cylinder() -> None:
    ents = Entities()
    primitives.cone(ents, (0, 0, 0), 500, 1000, 32, top_radius=250)
    assert is_closed_manifold(ents) and len(ents.faces) == 34
    ents2 = Entities()
    primitives.cylinder(ents2, (0, 0, 0), 100, 1000, 12, axis=primitives.axis_vector("x"))
    lo, hi = ents2.bounds()
    assert hi[0] == pytest.approx(1000) and lo[0] == pytest.approx(0)


# ----------------------------------------------------------------- selectors


def test_selectors_on_raw_box_and_group() -> None:
    model = Model()
    items = box_in(model.entities)
    picks = candidate_faces(items)
    assert len(picks) == 6
    top = select_faces(picks, "top")
    assert len(top) == 1 and np.allclose(top[0].centroid, (500, 500, 1000))
    assert np.allclose(select_faces(picks, "-y")[0].normal, (0, -1, 0))
    assert np.allclose(select_faces(picks, {"normal": [1, 0, 0]})[0].centroid, (1000, 500, 500))
    assert np.allclose(select_faces(picks, {"near": [500, 1200, 500]})[0].normal, (0, 1, 0))
    group = make_group(model, model.entities, items)
    group.transform = translation((0, 0, 1000))
    gpicks = candidate_faces([group])
    assert np.allclose(select_faces(gpicks, "top")[0].centroid, (500, 500, 2000))
    with pytest.raises(GeometryError):
        select_faces(gpicks, None)
    with pytest.raises(GeometryError):
        select_faces(gpicks, "sideways")


def test_free_face_selected_from_behind_is_flipped() -> None:
    ents = Entities()
    face = draw.rectangle(ents, (0, 0, 0), 100, 100).faces[0]
    picked = select_faces(candidate_faces([face]), "bottom")
    assert picked[0].flip and np.allclose(picked[0].normal, (0, 0, -1))
    box = Entities()
    faces = primitives.box(box, (0, 0, 0), (100, 100, 100))
    top_only = [f for f in faces if f.normal[2] > 0.5]
    with pytest.raises(GeometryError):
        select_faces(candidate_faces(top_only), "bottom")


def test_select_top_returns_all_coplanar_top_faces() -> None:
    ents = Entities()
    faces = primitives.box(ents, (0, 0, 0), (1000, 1000, 500))
    ents.add_line((500, 0, 500), (500, 1000, 500))
    picks = candidate_faces(list(ents.faces.values()))
    assert len(select_faces(picks, "top")) == 2
    assert isinstance(faces[0], Face)


def test_group_instances_share_definition() -> None:
    model = Model()
    group = make_group(model, model.entities, box_in(model.entities), component=True)
    second = add_instance(model.entities, group.definition, translation((2000, 0, 0)))
    face = select_faces(candidate_faces([second]), "top")[0]
    push_pull(group.definition.entities, face.face, 500)
    lo, hi = model.bounds()
    assert hi[2] == pytest.approx(1500), "editing the definition changes every instance"
