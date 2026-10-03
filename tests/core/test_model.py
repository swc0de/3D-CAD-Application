"""Tests for the model, components, placements and change logs."""

import numpy as np
import pytest

from pymodeler.core.components import add_instance, remove_instance
from pymodeler.core.materials import Material
from pymodeler.core.model import Model
from pymodeler.core.transform import translation
from pymodeler.ops.extrude import push_pull


def make_post(model: Model):
    post = model.add_definition("Post")
    face = post.entities.add_face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)])[0]
    push_pull(post.entities, face, 1000)
    return post


def test_definition_names_are_unique() -> None:
    model = Model()
    assert model.add_definition("Post").name == "Post"
    assert model.add_definition("Post").name == "Post#2"
    assert model.add_definition("Group", is_group=True).is_group


def test_instances_and_bounds() -> None:
    model = Model()
    post = make_post(model)
    for i in range(5):
        add_instance(model.entities, post, translation((i * 1000, 0, 0)))
    lo, hi = model.bounds()
    assert np.allclose(lo, (0, 0, 0)) and np.allclose(hi, (4100, 100, 1000))
    assert len(post.instances) == 5
    assert model.stats()["instances"] == 5


def test_nested_instances_and_material_inheritance() -> None:
    model = Model()
    model.add_material(Material("red"))
    post = make_post(model)
    fence = model.add_definition("Fence")
    add_instance(fence.entities, post, translation((0, 0, 0)))
    add_instance(fence.entities, post, translation((500, 0, 0)))
    outer = add_instance(model.entities, fence, translation((0, 1000, 0)))
    outer.material = "red"
    placements = list(model.iter_placements())
    assert len(placements) == 4  # root, fence, two posts
    post_places = [p for p in placements if p.entities is post.entities]
    assert all(p.material == "red" for p in post_places)
    assert {round(p.transform[0, 3]) for p in post_places} == {0, 500}
    assert all(p.transform[1, 3] == pytest.approx(1000) for p in post_places)


def test_hidden_tags_skip_subtrees() -> None:
    model = Model()
    post = make_post(model)
    inst = add_instance(model.entities, post)
    model.add_tag("Posts", visible=False)
    inst.tag = "Posts"
    assert len(list(model.iter_placements())) == 1
    assert len(list(model.iter_placements(visible_only=False))) == 2


def test_remove_instance_and_purge() -> None:
    model = Model()
    post = make_post(model)
    inst = add_instance(model.entities, post)
    remove_instance(model.entities, inst)
    assert not inst.alive and not post.instances
    assert model.purge_unused_definitions() == 1
    assert "Post" not in model.definitions


def test_erase_dispatches_instances() -> None:
    model = Model()
    post = make_post(model)
    inst = add_instance(model.entities, post)
    model.entities.erase([inst])
    assert not model.entities.instances


def test_change_log_tracks_splits_and_erasures() -> None:
    model = Model()
    ents = model.entities
    face = ents.add_face([(0, 0, 0), (1000, 0, 0), (1000, 1000, 0), (0, 1000, 0)])[0]
    with model.registry.tracking() as log:
        ents.add_line((500, 0, 0), (500, 1000, 0))
    assert len(log.successors(face.id, model.registry)) == 2
    assert log.net_created(model.registry), "the new halves and the split line are created"
    with model.registry.tracking() as log:
        ents.erase_faces(list(ents.faces.values()))
    assert face.id in log.erased
    assert log.successors(face.id, model.registry) == []
