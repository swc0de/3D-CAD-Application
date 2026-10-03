"""The native ``.pym`` format: the full model topology as JSON.

Unlike mesh formats, ``.pym`` keeps everything needed to keep editing: faces with
holes, edge styling and curves, groups and components, materials, tags and units.
The build script that produced a model can be embedded under ``"source"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from pymodeler.core.components import ComponentDefinition, add_instance
from pymodeler.core.entities import Entities
from pymodeler.core.guides import Guide
from pymodeler.core.materials import ColorError, Material
from pymodeler.core.model import Model
from pymodeler.core.tags import Tag

FORMAT_NAME = "pymodeler"
FORMAT_VERSION = 1


class ModelFormatError(ValueError):
    """Raised when a ``.pym`` file is malformed."""


def model_to_dict(model: Model, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Serialise a model to a JSON-compatible dict."""
    data: dict[str, Any] = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "units": model.units,
        "name": model.name,
        "description": model.description,
        "materials": {name: m.to_dict() for name, m in model.materials.items()},
        "tags": {name: t.to_dict() for name, t in model.tags.items()},
        "definitions": [
            {
                "name": d.name,
                "is_group": d.is_group,
                "description": d.description,
                "entities": _entities_to_dict(d.entities),
            }
            for d in model.definitions.values()
        ],
        "entities": _entities_to_dict(model.entities),
    }
    if model.guides:
        data["guides"] = [g.to_dict() for g in model.guides]
    if source is not None:
        data["source"] = source
    return data


def _entities_to_dict(ents: Entities) -> dict[str, Any]:
    """Serialise one collection with index-based topology."""
    index = {v.id: i for i, v in enumerate(ents.vertices.values())}
    vertices = [[round(c, 6) for c in v._t] for v in ents.vertices.values()]
    edges = []
    for e in ents.edges.values():
        item: dict[str, Any] = {"v": [index[e.v1.id], index[e.v2.id]]}
        for flag in ("soft", "smooth", "hidden"):
            if getattr(e, flag):
                item[flag] = True
        if e.curve is not None:
            item["curve"] = e.curve
        if e.tag:
            item["tag"] = e.tag
        edges.append(item)
    faces = []
    for f in ents.faces.values():
        item = {"loops": [[index[v.id] for v in loop] for loop in f.loops]}
        for key in ("material", "back_material", "tag"):
            if getattr(f, key):
                item[key] = getattr(f, key)
        if f.hidden:
            item["hidden"] = True
        faces.append(item)
    instances = []
    for inst in ents.instances.values():
        item = {
            "definition": inst.definition.name,
            "transform": [round(float(x), 9) for x in inst.transform.reshape(-1)],
        }
        for key in ("name", "material", "tag"):
            if getattr(inst, key):
                item[key] = getattr(inst, key)
        if inst.hidden:
            item["hidden"] = True
        instances.append(item)
    return {"vertices": vertices, "edges": edges, "faces": faces, "instances": instances}


def model_from_dict(data: Any) -> Model:
    """Rebuild a model from :func:`model_to_dict` output.

    Raises:
        ModelFormatError: if the data is not a valid ``.pym`` document.
    """
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise ModelFormatError("not a PyModeler model (missing \"format\": \"pymodeler\")")
    if data.get("version") != FORMAT_VERSION:
        raise ModelFormatError(f"unsupported .pym version {data.get('version')!r}")
    try:
        model = Model(units=data.get("units", "mm"))
        model.name = str(data.get("name", ""))
        model.description = str(data.get("description", ""))
        for name, spec in data.get("materials", {}).items():
            model.add_material(Material.from_spec(name, spec))
        for name, spec in data.get("tags", {}).items():
            model.tags[name] = Tag.from_spec(name, spec)
        definitions: dict[str, ComponentDefinition] = {}
        for item in data.get("definitions", []):
            d = ComponentDefinition(model.registry, item["name"], bool(item.get("is_group", False)))
            d.description = str(item.get("description", ""))
            model.definitions[d.name] = definitions[d.name] = d
        curves: dict[int, int] = {}
        for item in data.get("definitions", []):
            _fill_entities(definitions[item["name"]].entities, item["entities"], definitions, curves)
        _fill_entities(model.entities, data.get("entities", {}), definitions, curves)
        model.guides = [Guide.from_dict(g) for g in data.get("guides", [])]
    except ModelFormatError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, ColorError) as exc:
        raise ModelFormatError(f"corrupt model data: {exc}") from exc
    return model


def _fill_entities(
    ents: Entities,
    data: dict[str, Any],
    definitions: dict[str, ComponentDefinition],
    curves: dict[int, int],
) -> None:
    """Recreate one collection's topology exactly (no sticky processing)."""
    verts = [ents._create_vertex(p) for p in data.get("vertices", [])]
    for item in data.get("edges", []):
        i, j = item["v"]
        e = ents._create_edge(verts[i], verts[j])
        e.soft = bool(item.get("soft", False))
        e.smooth = bool(item.get("smooth", False))
        e.hidden = bool(item.get("hidden", False))
        e.tag = item.get("tag")
        if item.get("curve") is not None:
            e.curve = curves.setdefault(int(item["curve"]), ents.registry.new_id())
    for item in data.get("faces", []):
        loops = [[verts[i] for i in loop] for loop in item["loops"]]
        f = ents._create_face(loops)
        f.material = item.get("material")
        f.back_material = item.get("back_material")
        f.tag = item.get("tag")
        f.hidden = bool(item.get("hidden", False))
    for item in data.get("instances", []):
        name = item["definition"]
        if name not in definitions:
            raise ModelFormatError(f"instance refers to unknown definition {name!r}")
        matrix = np.array(item["transform"], dtype=float).reshape(4, 4)
        inst = add_instance(ents, definitions[name], matrix)
        inst.name = item.get("name", "")
        inst.material = item.get("material")
        inst.tag = item.get("tag")
        inst.hidden = bool(item.get("hidden", False))


def save_model(model: Model, path: str | Path, source: dict[str, Any] | None = None) -> None:
    """Write a model to a ``.pym`` file."""
    Path(path).write_text(json.dumps(model_to_dict(model, source), indent=1), encoding="utf-8")


def load_model(path: str | Path) -> Model:
    """Read a model from a ``.pym`` file.

    Raises:
        ModelFormatError: if the file is not valid JSON or not a ``.pym`` document.
        OSError: if the file cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelFormatError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    return model_from_dict(data)


def load_source(path: str | Path) -> dict[str, Any] | None:
    """The build script embedded in a ``.pym`` file, if any."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = data.get("source") if isinstance(data, dict) else None
    return source if isinstance(source, dict) else None
