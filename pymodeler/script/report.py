"""Machine-readable build reports: sizes and positions of every named object."""

from __future__ import annotations

from typing import Any

import numpy as np

from pymodeler.core.analysis import faces_form_closed_surface, is_closed_manifold
from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Entity, Face
from pymodeler.core.units import from_mm
from pymodeler.ops.library import target_bounds
from pymodeler.script.engine import BuildResult


def _num(v: float, unit: str) -> float:
    return round(from_mm(float(v), unit), 4)


def _box(box: tuple[np.ndarray, np.ndarray] | None, unit: str) -> dict[str, Any] | None:
    if box is None:
        return None
    lo, hi = box
    return {
        "min": [_num(c, unit) for c in lo],
        "max": [_num(c, unit) for c in hi],
        "size": [_num(c, unit) for c in hi - lo],
    }


def is_solid(entities: list[Entity]) -> bool:
    """Whether a named object is a closed solid (raw faces, groups or components)."""
    faces = [e for e in entities if isinstance(e, Face)]
    instances = [e for e in entities if isinstance(e, ComponentInstance)]
    if not faces and not instances:
        return False
    if faces and not faces_form_closed_surface(faces):
        return False
    return all(_instance_solid(i) for i in instances)


def _instance_solid(inst: ComponentInstance) -> bool:
    ents = inst.definition.entities
    if ents.faces and not is_closed_manifold(ents):
        return False
    if not ents.faces and not ents.instances:
        return False
    return all(_instance_solid(child) for child in ents.instances.values())


def build_report(result: BuildResult) -> dict[str, Any]:
    """Summary of a build in the model's units."""
    model = result.model
    unit = model.units
    objects = {}
    for name, parts in result.refs.items():
        items = [e for part in parts for e in part]
        objects[name] = {
            "parts": len(parts),
            "bounds": _box(target_bounds(items), unit),
            "solid": is_solid(items),
        }
    return {
        "ok": True,
        "units": unit,
        "bounds": _box(model.bounds(), unit),
        "stats": model.stats(),
        "steps_run": len(result.records),
        "objects": objects,
        "components": {k: {"instances": len(v.instances), "bounds": _box(v.local_bounds(), unit)}
                       for k, v in result.components.items()},
    }
