"""Shared helpers for build-script tests."""

from typing import Any

import numpy as np

from pymodeler.core.analysis import is_closed_manifold
from pymodeler.core.components import ComponentInstance
from pymodeler.ops.library import target_bounds
from pymodeler.script.engine import BuildResult, build_script


def build(steps: list[dict[str, Any]], **top: Any) -> BuildResult:
    """Validate and build a script made of ``steps``."""
    return build_script({"version": 1, "steps": steps, **top})


def parts(result: BuildResult, name: str) -> list:
    """Parts of a reference; ``name[i]`` selects one part."""
    if name.endswith("]"):
        base, _, index = name[:-1].partition("[")
        return [result.refs[base][int(index)]]
    return result.refs[name]


def bounds(result: BuildResult, name: str) -> tuple[np.ndarray, np.ndarray]:
    """World bounds of a named reference."""
    box = target_bounds([e for part in parts(result, name) for e in part])
    assert box is not None, f"{name} has no geometry"
    return box


def size(result: BuildResult, name: str) -> list[float]:
    lo, hi = bounds(result, name)
    return [round(float(v), 3) for v in hi - lo]


def instances(result: BuildResult, name: str) -> list[ComponentInstance]:
    return [e for part in parts(result, name) for e in part if isinstance(e, ComponentInstance)]


def group_is_solid(inst: ComponentInstance) -> bool:
    return is_closed_manifold(inst.definition.entities)
