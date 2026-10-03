"""Generate the build-script JSON Schema from the op registry.

The checked-in ``schema/build_script.schema.json`` is produced by :func:`write_schema`;
a test makes sure it matches the registry, so the schema can never drift from the code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pymodeler.core.units import MM_PER_UNIT
from pymodeler.ops.registry import ALIASES, OpSpec, Param, all_ops
from pymodeler.ops.selectors import DIRECTIONS
from pymodeler.script.values import ANCHORS, AXES

SCHEMA_ID = "https://github.com/swc0de/3D-CAD-Application/schema/build_script.schema.json"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "build_script.schema.json"
UNIT_NAMES = sorted(MM_PER_UNIT)
FACE_WORDS = sorted(set(DIRECTIONS) | {"all", "largest", "smallest"})


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def base_defs() -> dict[str, Any]:
    """Reusable value schemas."""
    value = {"type": ["number", "string"], "description": "number, unit string (\"2.4m\") or expression (\"$w / 2\")"}
    point_array = {"type": "array", "items": _ref("value"), "minItems": 2, "maxItems": 3}
    return {
        "value": value,
        "boolean": {"type": ["boolean", "string"]},
        "integer": {"type": ["integer", "string"]},
        "point": {"anyOf": [point_array, {"type": "string"}], "description": "[x, y, z] (z optional)"},
        "points": {"type": "array", "items": _ref("point"), "minItems": 1},
        "direction": {
            "anyOf": [
                {"type": "string", "description": f"axis name: {', '.join(AXES)}"},
                {"type": "array", "items": _ref("value"), "minItems": 3, "maxItems": 3},
            ]
        },
        "target": {
            "anyOf": [
                {"type": "string", "minLength": 1},
                {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1},
            ],
            "description": "id of an earlier step, 'id[n]' for one part, a list of ids, or '*'",
        },
        "face": {
            "anyOf": [
                {"enum": FACE_WORDS},
                _selector_object("normal", _ref("direction")),
                _selector_object("near", _ref("point")),
                _selector_object("index", _ref("integer")),
            ]
        },
        "repeat": {
            "type": "object",
            "properties": {
                "count": _ref("integer"),
                "offset": _ref("point"),
                "rotate": {
                    "type": "object",
                    "properties": {"angle": _ref("value"), "axis": _ref("direction"), "center": _ref("point")},
                    "required": ["angle"],
                    "additionalProperties": False,
                },
                "var": {"type": "string", "description": "name of the index variable (default i)"},
                "values": {"type": "array", "description": "iterate over these values instead of a count"},
                "as": {"type": "string", "description": "name of the value variable (default value)"},
            },
            "additionalProperties": False,
        },
        "variable": {
            "anyOf": [
                {"type": ["number", "string", "boolean"]},
                {"type": "array", "items": {"type": ["number", "string", "boolean", "array"]}},
            ]
        },
        "steps": {"type": "array", "items": _ref("step")},
    }


def _selector_object(key: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {key: schema}, "required": [key], "additionalProperties": False}


def param_schema(param: Param) -> dict[str, Any]:
    """JSON Schema of one parameter."""
    t = param.type
    simple = {
        "length": _ref("value"), "number": _ref("value"), "angle": _ref("value"), "integer": _ref("integer"),
        "bool": _ref("boolean"), "string": {"type": "string"}, "point": _ref("point"), "vector": _ref("point"),
        "direction": _ref("direction"), "points": _ref("points"), "target": _ref("target"), "face": _ref("face"),
        "component": {"type": "string", "minLength": 1}, "material": {"type": ["string", "null"]},
        "steps": _ref("steps"),
        "variables": {"type": "object", "additionalProperties": _ref("variable")},
    }
    if t in simple:
        schema = dict(simple[t])
    elif t == "enum":
        schema = {"enum": list(param.choices or ())}
    elif t == "loops":
        schema = {"type": "array", "items": _ref("points")}
    elif t in ("size", "scale"):
        schema = {"anyOf": [_ref("value"), {"type": "array", "items": _ref("value"), "minItems": 3, "maxItems": 3}]}
    elif t == "anchor":
        schema = {"anyOf": [{"enum": list(ANCHORS)}, _ref("point")]}
    elif t == "rotation":
        schema = {"anyOf": [_ref("value"), {
            "type": "object", "properties": {"axis": _ref("direction"), "angle": _ref("value")},
            "required": ["angle"], "additionalProperties": False}]}
    elif t == "path":
        schema = {"anyOf": [_ref("points"), _ref("target")]}
    elif t == "depth":
        schema = {"anyOf": [{"const": "through"}, _ref("value")]}
    else:  # pragma: no cover - guarded by a test over all ops
        raise ValueError(f"no schema for parameter type {t!r}")
    if param.help:
        schema["description"] = param.help
    if param.default is not None:
        schema["default"] = param.default
    return schema


def common_properties() -> dict[str, Any]:
    """Keys every step may use."""
    return {
        "id": {"type": "string", "minLength": 1, "description": "name for what this step creates"},
        "comment": {"type": "string"},
        "repeat": _ref("repeat"),
        "if": {"anyOf": [{"type": "boolean"}, _ref("value")], "description": "skip the step when false/0"},
        "material": {"type": ["string", "null"], "description": "paint what this step creates"},
        "tag": {"type": "string", "description": "put what this step creates on a tag"},
        "in": {"type": "string", "description": "run inside this group or component"},
    }


def op_schema(spec: OpSpec) -> dict[str, Any]:
    """Schema of one op's step object."""
    names = [spec.name] + [a for a, target in ALIASES.items() if target == spec.name]
    props: dict[str, Any] = {"op": {"enum": names}}
    props.update(common_properties())
    required: list[str] = ["op"]
    any_of: list[dict[str, Any]] = []
    for p in spec.params:
        props[p.name] = param_schema(p)
        for alias in p.aliases:
            props[alias] = param_schema(p)
        if p.required:
            if p.aliases:
                any_of.append({"anyOf": [{"required": [n]} for n in (p.name, *p.aliases)]})
            else:
                required.append(p.name)
    for group in spec.one_of:
        options = []
        for name in group:
            param = spec.param(name)
            options.extend({"required": [n]} for n in (name, *(param.aliases if param else ())))
        any_of.append({"anyOf": options})
    schema: dict[str, Any] = {
        "type": "object",
        "description": spec.summary,
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    if any_of:
        schema["allOf"] = any_of
    return schema


def build_schema() -> dict[str, Any]:
    """The complete build-script schema."""
    ops = all_ops()
    defs = base_defs()
    op_names: list[str] = []
    branches = []
    for spec in ops:
        defs[f"op_{spec.name}"] = op_schema(spec)
        names = [spec.name] + [a for a, t in ALIASES.items() if t == spec.name]
        op_names.extend(names)
        branches.append({"if": {"properties": {"op": {"enum": names}}, "required": ["op"]},
                         "then": _ref(f"op_{spec.name}")})
    defs["step"] = {
        "type": "object",
        "required": ["op"],
        "properties": {"op": {"enum": op_names}},
        "allOf": branches,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "PyModeler build script",
        "description": "An ordered list of modeling steps that builds a 3D model (Z up, millimetres by default).",
        "type": "object",
        "required": ["version", "steps"],
        "properties": {
            "$schema": {"type": "string"},
            "version": {"const": 1},
            "units": {"enum": UNIT_NAMES, "default": "mm"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "variables": {"type": "object", "additionalProperties": _ref("variable")},
            "materials": {
                "type": "object",
                "additionalProperties": {"anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 4},
                    {"type": "object", "properties": {
                        "color": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "number"}}]},
                        "opacity": {"type": "number", "minimum": 0, "maximum": 1}},
                     "additionalProperties": False},
                ]},
            },
            "tags": {
                "type": "object",
                "additionalProperties": {"anyOf": [{"type": "null"}, {
                    "type": "object",
                    "properties": {"visible": {"type": "boolean"}, "color": {"type": "string"}},
                    "additionalProperties": False}]},
            },
            "steps": _ref("steps"),
        },
        "additionalProperties": False,
        "$defs": defs,
    }


def schema_text() -> str:
    """The schema serialised exactly as it is checked in."""
    return json.dumps(build_schema(), indent=2) + "\n"


def write_schema(path: Path = SCHEMA_PATH) -> Path:
    """Write the schema file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_text(), encoding="utf-8")
    return path
