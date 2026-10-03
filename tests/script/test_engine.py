"""Tests for the build-script engine: values, references, repeat and every op."""

import numpy as np
import pytest

from pymodeler.core.analysis import is_closed_manifold
from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Edge, Face
from pymodeler.script.engine import Engine, build_script
from pymodeler.script.errors import ScriptError, ScriptValidationError

from .helpers import bounds, build, group_is_solid, instances, size

# ----------------------------------------------------------------- basics


def test_user_example_from_the_brief() -> None:
    result = build(
        [
            {"op": "rectangle", "id": "floor", "origin": [0, 0, 0], "width": "$w", "depth": "$d"},
            {"op": "push_pull", "target": "floor", "face": "top", "distance": "$h"},
            {"op": "make_group", "target": "floor", "name": "Room"},
            {"op": "paint", "target": "Room", "material": "brick"},
            {"op": "component", "id": "post", "steps": [
                {"op": "circle", "id": "c", "center": [0, 0, 0], "radius": 50, "segments": 24},
                {"op": "push_pull", "target": "c", "face": "top", "distance": 1000}]},
            {"op": "place", "component": "post", "position": [0, -1000, 0],
             "repeat": {"count": 5, "offset": [1000, 0, 0]}},
        ],
        units="mm",
        variables={"w": 4000, "d": 3000, "h": 2400, "wall": 200},
        materials={"brick": {"color": "#a0522d"}, "glass": {"color": "#88ccff", "opacity": 0.4}},
    )
    (room,) = instances(result, "Room")
    assert room.material == "brick" and room.is_group and group_is_solid(room)
    assert result.refs["floor"] == result.refs["Room"], "the grouped rectangle's id now names the group"
    assert size(result, "Room") == [4000, 3000, 2400]
    post = result.components["post"]
    assert len(post.instances) == 5 and is_closed_manifold(post.entities)
    xs = sorted(round(i.transform[0, 3]) for i in post.instances)
    assert xs == [0, 1000, 2000, 3000, 4000]
    assert result.model.materials["glass"].opacity == 0.4


def test_units_and_unit_strings() -> None:
    result = build([{"op": "box", "id": "b", "size": ["1.2m", "50cm", "$h"]}], units="m", variables={"h": "300mm"})
    assert size(result, "b") == [1200, 500, 300]
    assert result.model.units == "m"


def test_variables_chain_and_set() -> None:
    result = build(
        [
            {"op": "box", "id": "table", "size": ["$w", 800, 750]},
            {"op": "set", "variables": {"top": "@table.zmax", "half": "$w / 2"}},
            {"op": "box", "id": "lamp", "origin": ["$half", 400, "$top"], "size": [100, 100, 300]},
        ],
        variables={"w": 1600},
    )
    lo, _ = bounds(result, "lamp")
    assert list(lo) == [800, 400, 750]


def test_geometry_queries_with_index() -> None:
    result = build([
        {"op": "box", "id": "step", "origin": [0, "$i * 300", "$i * 200"], "size": [1000, 300, 200],
         "repeat": {"count": 4}},
        {"op": "box", "id": "landing", "origin": [0, "@step[3].ymax", 0], "size": [1000, 1000, "@step.zmax"]},
    ])
    lo, hi = bounds(result, "landing")
    assert lo[1] == 1200 and hi[2] == 800
    assert len(result.refs["step"]) == 4


def test_repeat_rotate_values_and_if() -> None:
    result = build([
        {"op": "box", "id": "spoke", "origin": [1000, -50, 0], "size": [500, 100, 100],
         "repeat": {"count": 4, "rotate": {"angle": 90, "axis": "z", "center": [0, 0, 0]}}},
        {"op": "cylinder", "id": "legs", "center": "$value", "radius": 20, "height": 700,
         "repeat": {"values": [[0, 0], [1000, 0], [0, 500], [1000, 500]]}},
        {"op": "box", "id": "even", "origin": ["$k * 200", 3000, 0], "size": [100, 100, 100],
         "if": "$k % 2 == 0", "repeat": {"count": 5, "var": "k"}},
    ])
    lo, hi = bounds(result, "spoke")
    assert np.allclose(lo, (-1500, -1500, 0)) and np.allclose(hi, (1500, 1500, 100))
    assert len(result.refs["legs"]) == 4
    assert len(result.refs["even"]) == 3


def test_polar_repeat_rotates_group_axes() -> None:
    result = build([{"op": "box", "id": "b", "origin": [1000, 0, 0], "size": [500, 100, 100],
                     "repeat": {"count": 2, "rotate": {"angle": 90}}}])
    second = result.refs["b"][1][0]
    assert isinstance(second, ComponentInstance)
    assert np.allclose(second.transform[:3, 0], (0, 1, 0)), "the copy's X axis points along +Y"


def test_common_material_and_tag() -> None:
    result = build(
        [{"op": "box", "id": "b", "size": [100, 100, 100], "material": "oak", "tag": "Furniture"},
         {"op": "rectangle", "id": "r", "origin": [500, 0, 0], "width": 100, "depth": 100, "material": "#ff0000"}],
        materials={"oak": "#c19a6b"},
    )
    (inst,) = instances(result, "b")
    assert inst.material == "oak" and inst.tag == "Furniture" and "Furniture" in result.model.tags
    face = next(e for e in result.refs["r"][0] if isinstance(e, Face))
    assert face.material == "#ff0000" and face.back_material == "#ff0000"


def test_in_runs_inside_a_group() -> None:
    result = build([
        {"op": "box", "id": "walls", "size": [4000, 200, 2400]},
        {"op": "move", "target": "walls", "by": [0, 0, 100]},
        {"op": "rectangle", "id": "sign", "in": "walls", "origin": [100, 0, 1000], "width": 500,
         "depth": 300, "plane": "xz"},
    ])
    (walls,) = instances(result, "walls")
    face = next(e for e in result.refs["sign"][0] if isinstance(e, Face))
    assert face.parent is walls.definition.entities
    assert face.centroid()[2] == pytest.approx(1050), "world z 1150 is local z 1050 in the raised group"
    assert len(walls.definition.entities.faces) == 7


# ----------------------------------------------------------------- draw ops


def test_draw_ops() -> None:
    result = build([
        {"op": "line", "id": "l", "points": [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0]], "closed": True},
        {"op": "line", "id": "seg", "from": [0, 3000, 0], "to": [500, 3000, 0]},
        {"op": "rectangle", "id": "wall", "origin": [2000, 0, 0], "width": 1000, "height": 2000, "plane": "xz"},
        {"op": "circle", "id": "c", "center": [5000, 0, 0], "radius": 300, "segments": 16},
        {"op": "arc", "id": "a", "center": [7000, 0, 0], "radius": 300, "end_angle": 180, "close": "chord"},
        {"op": "polygon", "id": "p", "center": [9000, 0, 0], "radius": 300, "sides": 6},
        {"op": "face", "id": "f", "points": [[11000, 0, 0], [12000, 0, 0], [11500, 800, 0]]},
    ])
    faces = {k: [e for e in result.refs[k][0] if isinstance(e, Face)] for k in ("l", "wall", "c", "a", "p", "f")}
    assert all(len(v) == 1 for v in faces.values())
    assert np.allclose(faces["wall"][0].normal, (0, -1, 0))
    assert len([e for e in result.refs["c"][0] if isinstance(e, Edge)]) == 16
    assert all(isinstance(e, Edge) for e in result.refs["seg"][0])


def test_rectangle_with_custom_normal() -> None:
    result = build([{"op": "rectangle", "id": "r", "width": 100, "depth": 200, "normal": "x"}])
    face = result.refs["r"][0][0]
    assert isinstance(face, Face) and np.allclose(face.normal, (1, 0, 0))


# ----------------------------------------------------------------- modify ops


def test_push_pull_grows_reference_and_handles_back_side() -> None:
    result = build([
        {"op": "rectangle", "id": "slab", "width": 1000, "depth": 1000},
        {"op": "push_pull", "target": "slab", "face": "bottom", "distance": 200},
    ])
    lo, hi = bounds(result, "slab")
    assert lo[2] == -200 and hi[2] == 0
    assert len([e for e in result.refs["slab"][0] if isinstance(e, Face)]) == 6


def test_push_pull_on_group_face() -> None:
    result = build([
        {"op": "box", "id": "b", "size": [1000, 1000, 1000]},
        {"op": "push_pull", "target": "b", "face": "+x", "distance": 500},
    ])
    assert size(result, "b") == [1500, 1000, 1000]


def test_opening_through_wall_group() -> None:
    result = build([
        {"op": "box", "id": "wall", "size": [4000, 200, 2400]},
        {"op": "opening", "target": "wall", "face": "front", "origin": [1000, 0, 0], "width": 900, "height": 2100},
        {"op": "opening", "target": "wall", "face": "back", "origin": [3500, 200, 900], "width": 1000,
         "height": 1000, "depth": 100},
    ])
    (wall,) = instances(result, "wall")
    from pymodeler.core.analysis import signed_volume

    expected = 4000 * 200 * 2400 - 900 * 200 * 2100 - 1000 * 100 * 1000
    assert is_closed_manifold(wall.definition.entities)
    assert signed_volume(wall.definition.entities) == pytest.approx(expected)


def test_offset_and_hollow_box() -> None:
    result = build([
        {"op": "box", "id": "tray", "size": [1000, 600, 100]},
        {"op": "offset", "target": "tray", "face": "top", "distance": 50},
        {"op": "push_pull", "target": "tray", "face": {"near": [500, 300, 100]}, "distance": -80},
    ])
    (tray,) = instances(result, "tray")
    from pymodeler.core.analysis import signed_volume

    assert signed_volume(tray.definition.entities) == pytest.approx(1000 * 600 * 100 - 900 * 500 * 80)


def test_follow_me_with_points_and_edges() -> None:
    result = build([
        {"op": "rectangle", "id": "prof", "origin": [0, -50, -50], "width": 100, "depth": 100, "plane": "yz"},
        {"op": "follow_me", "target": "prof", "path": [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]},
        {"op": "group", "id": "ring", "steps": [
            {"op": "circle", "id": "rail", "center": [0, 3000, 0], "radius": 500, "segments": 16},
            {"op": "erase", "target": "rail", "face": "top"},
            {"op": "circle", "id": "rprof", "center": [500, 3000, 0], "radius": 50, "segments": 8, "plane": "xz"},
            {"op": "follow_me", "target": "rprof", "path": "rail"}]},
    ])
    assert size(result, "prof") == [1050, 1050, 100]
    (ring,) = instances(result, "ring")
    faces = [f for f in ring.definition.entities.faces.values()]
    assert len(faces) == 8 * 16


def test_extrude_height_and_direction() -> None:
    result = build([
        {"op": "extrude", "id": "prism", "points": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]], "height": 500},
        {"op": "extrude", "id": "roof", "points": [[0, 0, 0], [4000, 0, 0], [2000, 0, 1000]],
         "direction": [0, 3000, 0]},
    ])
    assert size(result, "prism") == [1000, 1000, 500]
    assert size(result, "roof") == [4000, 3000, 1000]


# ----------------------------------------------------------------- transform ops


def test_move_rotate_scale_mirror() -> None:
    result = build([
        {"op": "box", "id": "a", "size": [1000, 500, 200]},
        {"op": "move", "target": "a", "by": [100, 0, 0]},
        {"op": "move", "target": "a", "to": [0, 2000, 0]},
        {"op": "rotate", "target": "a", "angle": 90},
        {"op": "scale", "target": "a", "factor": 2, "center": "bottom"},
        {"op": "box", "id": "m", "origin": [100, 0, 0], "size": [100, 100, 100]},
        {"op": "mirror", "target": "m", "axis": "x", "center": [0, 0, 0]},
    ])
    assert size(result, "a") == [1000, 2000, 400]
    lo, _ = bounds(result, "a")
    assert lo[2] == 0
    lo, hi = bounds(result, "m")
    assert lo[0] == -200 and hi[0] == -100


def test_scale_top_face_makes_taper() -> None:
    result = build([
        {"op": "box", "id": "b", "size": [1000, 1000, 1000]},
        {"op": "scale", "target": "b", "face": "top", "factor": 0.5},
    ])
    (inst,) = instances(result, "b")
    top = max(inst.definition.entities.faces.values(), key=lambda f: f.centroid()[2])
    assert top.area() == pytest.approx(250_000)
    assert is_closed_manifold(inst.definition.entities)


def test_copy_and_array() -> None:
    result = build([
        {"op": "box", "id": "a", "size": [100, 100, 100]},
        {"op": "copy", "id": "b", "target": "a", "by": [500, 0, 0]},
        {"op": "array", "id": "row", "target": "a", "count": 3, "offset": [0, 500, 0]},
        {"op": "array", "id": "ring", "target": "a", "count": 3, "angle": 90, "center": [0, 0, 0]},
        {"op": "push_pull", "target": "row[0]", "face": "top", "distance": 100},
    ])
    assert len(result.refs["row"]) == 3 and len(result.refs["ring"]) == 3
    assert size(result, "b") == [100, 100, 100]
    assert size(result, "row[0]") == [100, 100, 200]
    row0 = result.refs["row"][0][0]
    assert isinstance(row0, ComponentInstance)
    assert row0.definition is not instances(result, "a")[0].definition, "copied groups are unique"
    assert size(result, "a") == [100, 100, 100], "editing a copy leaves the original alone"


# ----------------------------------------------------------------- organise ops


def test_group_and_nested_ids() -> None:
    result = build([
        {"op": "group", "id": "table", "name": "Table", "steps": [
            {"op": "box", "id": "top", "origin": [0, 0, 700], "size": [1200, 800, 40]},
            {"op": "box", "id": "leg", "origin": "$value", "size": [50, 50, 700],
             "repeat": {"values": [[0, 0], [1150, 0], [0, 750], [1150, 750]]}}]},
        {"op": "move", "target": "Table", "by": [1000, 0, 0]},
    ])
    assert size(result, "table") == [1200, 800, 740]
    lo, _ = bounds(result, "leg[1]")
    assert lo[0] == 1000 + 1150, "nested ids follow the moved group"


def test_component_place_rotation_scale_and_paint_all() -> None:
    result = build([
        {"op": "component", "id": "chair", "name": "Chair", "steps": [
            {"op": "box", "size": [400, 400, 450], "group": False}]},
        {"op": "place", "id": "c1", "component": "chair", "position": [0, 0, 0], "rotation": 90},
        {"op": "place", "id": "c2", "component": "chair", "position": [1000, 0, 0],
         "rotation": {"axis": "z", "angle": 180}, "scale": [1, 1, 2]},
        {"op": "paint", "target": "chair", "material": "red"},
    ])
    defn = result.components["chair"]
    assert defn.name == "Chair" and len(defn.instances) == 2
    assert all(i.material == "red" for i in defn.instances)
    assert size(result, "c2") == [400, 400, 900]
    lo, hi = bounds(result, "c1")
    assert np.allclose(lo, (-400, 0, 0)) and np.allclose(hi, (0, 400, 450))


def test_make_component_explode_hide_tag_erase() -> None:
    result = build([
        {"op": "box", "id": "a", "size": [100, 100, 100], "group": False},
        {"op": "make_component", "id": "comp", "target": "a", "name": "Block"},
        {"op": "place", "id": "b", "component": "comp", "position": [500, 0, 0]},
        {"op": "set_tag", "target": "b", "tag": "Extra"},
        {"op": "hide", "target": "b"},
        {"op": "box", "id": "g", "origin": [0, 1000, 0], "size": [100, 100, 100]},
        {"op": "explode", "target": "g"},
        {"op": "box", "id": "gone", "origin": [0, 3000, 0], "size": [100, 100, 100]},
        {"op": "erase", "target": "gone"},
    ])
    model = result.model
    assert result.components["comp"].name == "Block"
    (b,) = instances(result, "b")
    assert b.hidden and b.tag == "Extra"
    assert len(model.entities.faces) == 6, "the exploded box is raw geometry now"
    assert not result.refs["gone"][0]


def test_erase_single_face_of_group() -> None:
    result = build([
        {"op": "box", "id": "b", "size": [100, 100, 100]},
        {"op": "erase", "target": "b", "face": "top"},
    ])
    (inst,) = instances(result, "b")
    assert len(inst.definition.entities.faces) == 5
    assert len(inst.definition.entities.edges) == 12


def test_intersect_groups() -> None:
    result = build([
        {"op": "box", "id": "a", "size": [1000, 1000, 1000]},
        {"op": "box", "id": "b", "origin": [500, 500, 500], "size": [1000, 1000, 1000]},
        {"op": "intersect", "id": "lines", "target": "a", "with": "b"},
    ])
    assert len(result.refs["lines"][0]) > 0
    assert len(instances(result, "a")[0].definition.entities.faces) == 9


def test_primitives_via_json() -> None:
    result = build([
        {"op": "box", "id": "b", "width": 100, "depth": 200, "height": 300, "center": True},
        {"op": "cylinder", "id": "c", "center": [1000, 0, 0], "radius": 100, "height": 500, "axis": "x"},
        {"op": "cone", "id": "k", "center": [2000, 0, 0], "radius": 200, "height": 400, "top_radius": 50},
        {"op": "sphere", "id": "s", "center": [3000, 0, 500], "radius": 250, "segments": 12, "rings": 6},
        {"op": "box", "id": "raw", "origin": [5000, 0, 0], "size": 100, "group": False},
    ])
    assert size(result, "b") == [100, 200, 300]
    assert bounds(result, "b")[0][0] == -50
    assert size(result, "c")[0] == 500
    for name in ("b", "c", "k", "s"):
        assert group_is_solid(instances(result, name)[0]), name
    assert all(isinstance(e, (Face, Edge)) for e in result.refs["raw"][0])


# ----------------------------------------------------------------- errors


def test_runtime_errors_name_the_step() -> None:
    with pytest.raises(ScriptError) as info:
        build([{"op": "box", "id": "b", "size": [100, 100, 100]},
               {"op": "push_pull", "target": "b", "distance": 10}])
    assert "step 2 (push_pull)" in str(info.value) and "face" in str(info.value)
    with pytest.raises(ScriptError) as info:
        build([{"op": "rectangle", "id": "r", "width": 0, "depth": 10}])
    assert "step 1 (rectangle 'r')" in str(info.value)
    with pytest.raises(ScriptError) as info:
        build([{"op": "box", "id": "b", "size": [1, 1, 1],
                "repeat": {"count": 2, "offset": [0, 0, 0]}},
               {"op": "move", "target": "b[5]", "by": [1, 0, 0]}])
    assert "out of range" in str(info.value)


def test_nested_error_path() -> None:
    with pytest.raises(ScriptError) as info:
        build([{"op": "group", "id": "g", "steps": [{"op": "circle", "radius": -5}]}])
    assert "step 1 (group 'g') > step 1 (circle)" in str(info.value)


def test_validation_runs_before_building() -> None:
    with pytest.raises(ScriptValidationError) as info:
        build_script({"version": 1, "steps": [{"op": "boxx"}]})
    assert "did you mean 'box'" in str(info.value)


def test_engine_rejects_bad_units() -> None:
    with pytest.raises(ScriptError):
        Engine({"version": 1, "units": "furlong", "steps": []})


def test_unknown_material_message() -> None:
    with pytest.raises(ScriptError) as info:
        build([{"op": "box", "size": [1, 1, 1], "material": "brik"}], materials={"brick": "#a0522d"})
    assert "did you mean 'brick'" in str(info.value)
