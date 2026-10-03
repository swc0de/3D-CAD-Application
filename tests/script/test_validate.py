"""Tests for build-script validation messages."""

import pytest

from pymodeler.ops.registry import all_ops
from pymodeler.script.errors import Problem
from pymodeler.script.validate import _check_structure, validate_script


def messages(script: dict) -> list[str]:
    return [str(p) for p in validate_script(script)]


def one(script: dict) -> str:
    found = messages(script)
    assert len(found) == 1, found
    return found[0]


def test_valid_script_has_no_problems() -> None:
    script = {"version": 1, "units": "m", "variables": {"w": 4, "h": "$w / 2"},
              "steps": [{"op": "box", "id": "b", "size": ["$w", 1, "$h"]},
                        {"op": "push_pull", "target": "b", "face": "top", "distance": "100mm"}]}
    assert messages(script) == []


@pytest.mark.parametrize(
    "script, fragment",
    [
        ([], "must be a JSON object"),
        ({"steps": []}, "missing top-level key 'version'"),
        ({"version": 2, "steps": []}, "unsupported version"),
        ({"version": 1, "units": "parsec", "steps": []}, "unknown units"),
        ({"version": 1, "steps": [], "unit": "mm"}, "did you mean 'units'"),
        ({"version": 1, "steps": [{"op": "push_pul"}]}, "unknown op 'push_pul' (did you mean 'push_pull'?)"),
        ({"version": 1, "steps": [{}]}, "missing \"op\""),
        ({"version": 1, "steps": ["x"]}, "must be an object"),
        ({"version": 1, "steps": [{"op": "circle"}]}, "step 1 (circle): missing required parameter 'radius'"),
        ({"version": 1, "steps": [{"op": "circle", "radius": 5, "radus": 5}]}, "unknown parameter 'radus'"),
        ({"version": 1, "steps": [{"op": "circle", "radius": [1, 2]}]}, "parameter 'radius': expected a length"),
        ({"version": 1, "steps": [{"op": "circle", "radius": "$r"}]}, "unknown variable $r"),
        ({"version": 1, "steps": [{"op": "circle", "radius": "5 +"}]}, "unexpected end"),
        ({"version": 1, "steps": [{"op": "rectangle", "width": 1, "depth": 1, "plane": "xx"}]}, "one of xy, xz, yz"),
        ({"version": 1, "steps": [{"op": "erase", "target": "nothing"}]}, "unknown id 'nothing'"),
        ({"version": 1, "steps": [{"op": "place", "component": "post"}]}, "unknown component 'post'"),
        ({"version": 1, "steps": [{"op": "box"}]}, "needs one of: 'size', 'width'"),
        ({"version": 1, "steps": [{"op": "box", "size": 1, "repeat": {"cont": 3}}]}, "did you mean 'count'"),
        ({"version": 1, "variables": {"a": "@b.zmax"}, "steps": []}, "not available in top-level variables"),
    ],
)
def test_problem_messages(script, fragment: str) -> None:
    joined = "\n".join(messages(script) if isinstance(script, dict) else [str(p) for p in validate_script(script)])
    assert fragment in joined


def test_paths_for_nested_steps() -> None:
    script = {"version": 1, "steps": [
        {"op": "group", "id": "g", "steps": [{"op": "box", "size": 1}, {"op": "circle"}]}]}
    assert one(script).startswith("step 1 (group 'g') > step 2 (circle)")


def test_scoping_of_ids_variables_and_components() -> None:
    script = {"version": 1, "steps": [
        {"op": "box", "id": "a", "size": "$i * 10 + 1", "repeat": {"count": 2}},
        {"op": "box", "id": "b", "size": "$v + 1", "repeat": {"values": [1, 2], "as": "v"}},
        {"op": "set", "variables": {"top": "@a.zmax"}},
        {"op": "box", "size": "$top + 1"},
        {"op": "component", "id": "post", "steps": [{"op": "box", "size": 1, "group": False}]},
        {"op": "place", "component": "post"},
        {"op": "make_group", "target": "b", "name": "Bees"},
        {"op": "paint", "target": "Bees", "material": "red"},
    ]}
    assert messages(script) == []
    bad = {"version": 1, "steps": [{"op": "box", "size": "$i"}]}
    assert "unknown variable $i" in one(bad)


def test_face_selectors_and_axis_words_are_not_expressions() -> None:
    script = {"version": 1, "steps": [
        {"op": "box", "id": "a", "size": 100},
        {"op": "push_pull", "target": "a", "face": "top", "distance": 10},
        {"op": "rotate", "target": "a", "angle": 45, "axis": "-z", "center": "bottom"},
        {"op": "opening", "target": "a", "face": "front", "origin": [10, 0, 10], "width": 20, "height": 20,
         "depth": "through"},
        {"op": "push_pull", "target": "a", "face": {"near": [0, 0, "$x"]}, "distance": 1},
    ]}
    found = messages(script)
    assert len(found) == 1 and "unknown variable $x" in found[0]


@pytest.mark.parametrize("spec", all_ops(), ids=lambda s: s.name)
def test_every_op_example_is_structurally_valid(spec) -> None:
    assert spec.example is not None, f"{spec.name} needs an example"
    problems: list[Problem] = []
    _check_structure(spec.example, spec, "example", problems)
    assert problems == []
