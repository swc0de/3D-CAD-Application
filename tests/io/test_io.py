"""Tests for the native format, mesh extraction, exporters and OBJ import."""

import json
import math
import struct

import numpy as np
import pytest
import trimesh

from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.core.components import add_instance
from pymodeler.core.materials import Material
from pymodeler.core.model import Model
from pymodeler.core.transform import translation
from pymodeler.io import UnsupportedFormatError, export_model, load_model_file
from pymodeler.io.mesh import build_mesh
from pymodeler.io.native import ModelFormatError, load_model, load_source, model_from_dict, model_to_dict
from pymodeler.io.obj_import import ObjImportError, import_obj
from pymodeler.ops import primitives
from pymodeler.ops.organize import make_group


def sample_model() -> Model:
    """A box group painted red, a component placed twice, and a raw cylinder."""
    model = Model(units="cm")
    model.add_material(Material("red", (1.0, 0.0, 0.0)))
    model.add_material(Material("glass", (0.5, 0.8, 1.0), 0.4))
    model.add_tag("Posts")
    primitives.box(model.entities, (0, 0, 0), (1000, 1000, 500))
    room = make_group(model, model.entities, [*model.entities.faces.values(), *model.entities.edges.values()], name="Room")
    room.material = "red"
    post_def = model.add_definition("Post")
    primitives.cylinder(post_def.entities, (0, 0, 0), 50, 1000, 12)
    for x in (2000, 3000):
        inst = add_instance(model.entities, post_def, translation((x, 0, 0)))
        inst.tag = "Posts"
    faces = primitives.box(model.entities, (0, 2000, 0), (500, 500, 500))
    faces[0].material = "glass"
    return model


def test_native_round_trip(tmp_path) -> None:
    model = sample_model()
    path = tmp_path / "m.pym"
    export_model(model, path, source={"version": 1, "steps": []})
    loaded = load_model(path)
    assert loaded.units == "cm"
    assert loaded.stats() == model.stats()
    assert set(loaded.materials) == {"red", "glass"}
    assert "Posts" in loaded.tags
    assert np.allclose(loaded.bounds()[0], model.bounds()[0])
    assert np.allclose(loaded.bounds()[1], model.bounds()[1])
    post = loaded.definitions["Post"]
    assert sum(e.soft for e in post.entities.edges.values()) == 12
    assert len({e.curve for e in post.entities.edges.values() if e.curve}) == 2
    assert is_closed_manifold(post.entities)
    assert load_source(path) == {"version": 1, "steps": []}
    # The round trip is stable (curve ids are renumbered on load, so ignore them).
    def strip(defs):
        return json.loads(json.dumps(defs).replace('"curve"', '"_c"'), object_hook=lambda d: {k: v for k, v in d.items() if k != "_c"})

    assert strip(model_to_dict(loaded)["definitions"]) == strip(model_to_dict(model)["definitions"])


def test_native_errors(tmp_path) -> None:
    with pytest.raises(ModelFormatError):
        model_from_dict({"format": "other"})
    with pytest.raises(ModelFormatError):
        model_from_dict({"format": "pymodeler", "version": 1, "entities": {"vertices": [[0, 0, 0]], "edges": [{"v": [0, 5]}]}})
    bad = tmp_path / "bad.pym"
    bad.write_text("{not json")
    with pytest.raises(ModelFormatError):
        load_model(bad)


def test_mesh_uses_inherited_materials_and_hides_tags() -> None:
    model = sample_model()
    mesh = build_mesh(model)
    assert "red" in mesh.parts and "glass" in mesh.parts
    with_posts = mesh.triangle_count
    model.tags["Posts"].visible = False
    assert build_mesh(model).triangle_count < with_posts
    # Soft edges of the cylinders are not drawn.
    assert len(mesh.edges) < sum(len(d.entities.edges) for d in model.definitions.values()) + len(model.entities.edges) + 24


def test_smooth_normals_on_cylinder_sides() -> None:
    model = Model()
    primitives.cylinder(model.entities, (0, 0, 0), 100, 100, 24)
    part = build_mesh(model).parts["Default"]
    _, normals = part.arrays()
    side = normals[np.abs(normals[:, 2]) < 0.5]
    # Smoothed normals are radial (not the flat face normals), so many distinct directions.
    assert len(np.unique(np.round(side, 4), axis=0)) == 24


@pytest.mark.parametrize("suffix", [".glb", ".gltf", ".obj", ".stl"])
def test_exports_load_in_trimesh(tmp_path, suffix) -> None:
    model = Model()
    primitives.box(model.entities, (0, 0, 0), (2000, 1000, 500))
    path = tmp_path / f"box{suffix}"
    export_model(model, path)
    loaded = trimesh.load(path, force="mesh")
    extents = sorted(np.round(loaded.extents, 6))
    if suffix == ".stl":
        assert extents == [500, 1000, 2000]  # millimetres
    else:
        assert extents == [0.5, 1.0, 2.0]  # metres
    assert len(loaded.faces) == 12


def test_gltf_is_y_up_with_materials(tmp_path) -> None:
    model = sample_model()
    path = tmp_path / "m.gltf"
    export_model(model, path)
    doc = json.loads(path.read_text())
    assert doc["asset"]["version"] == "2.0"
    names = {m["name"] for m in doc["materials"]}
    assert {"red", "glass", "Default"} <= names
    glass = next(m for m in doc["materials"] if m["name"] == "glass")
    assert glass["alphaMode"] == "BLEND"
    tops = [doc["accessors"][p["attributes"]["POSITION"]]["max"][1] for p in doc["meshes"][0]["primitives"]]
    assert max(tops) == pytest.approx(1.0), "the 1 m posts are the tallest thing, along +Y"


def test_glb_header(tmp_path) -> None:
    path = tmp_path / "m.glb"
    export_model(sample_model(), path)
    data = path.read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)


def test_empty_model_exports(tmp_path) -> None:
    for suffix in (".glb", ".stl", ".obj"):
        export_model(Model(), tmp_path / f"empty{suffix}")


def test_unsupported_format(tmp_path) -> None:
    with pytest.raises(UnsupportedFormatError):
        export_model(Model(), tmp_path / "m.3ds")
    with pytest.raises(UnsupportedFormatError):
        load_model_file(tmp_path / "m.skp")


def test_obj_round_trip_merges_triangles(tmp_path) -> None:
    model = Model()
    model.add_material(Material("red", (1.0, 0.0, 0.0)))
    faces = primitives.box(model.entities, (0, 0, 0), (1000, 1000, 1000))
    for f in faces:
        f.material = "red"
    path = tmp_path / "box.obj"
    export_model(model, path)
    imported = load_model_file(path)
    (group,) = imported.entities.instances.values()
    ents = group.definition.entities
    assert len(ents.faces) == 6, "triangles are merged back into quads"
    assert is_closed_manifold(ents)
    assert signed_volume(ents) == pytest.approx(1e9, rel=1e-6)
    assert "red" in imported.materials
    assert all(f.material == "red" for f in ents.faces.values())


def test_obj_import_quads_and_errors(tmp_path) -> None:
    path = tmp_path / "quad.obj"
    path.write_text("o plate\nv 0 0 0\nv 1 0 0\nv 1 0 1\nv 0 0 1\nf 1 2 3 4\n")
    model = Model()
    (group,) = import_obj(model, path, unit="m", up="y")
    face = next(iter(group.definition.entities.faces.values()))
    assert face.area() == pytest.approx(1e6)
    bad = tmp_path / "bad.obj"
    bad.write_text("v 0 0 0\nf 1 2 3\n")
    with pytest.raises(ObjImportError):
        import_obj(Model(), bad)


def test_obj_written_in_metres_y_up(tmp_path) -> None:
    model = Model()
    primitives.box(model.entities, (0, 0, 0), (1000, 2000, 3000))
    path = tmp_path / "b.obj"
    export_model(model, path)
    vs = np.array([[float(x) for x in line.split()[1:]] for line in path.read_text().splitlines() if line.startswith("v ")])
    assert vs[:, 1].max() == pytest.approx(3.0)  # height became Y
    assert vs[:, 2].min() == pytest.approx(-2.0)  # depth became -Z
    assert math.isclose(vs[:, 0].max(), 1.0)
