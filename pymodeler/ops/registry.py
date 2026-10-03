"""The operation registry: one declaration per modeling operation.

Each op declares typed parameters and a ``run(ctx, args)`` function.  The registry is
the single source of truth for

* the JSON build-script engine (which converts JSON values into typed arguments),
* the JSON Schema (``schema/build_script.schema.json``) and the reference docs,
* GUI commands (which build typed arguments directly and get undo for free).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from pymodeler.core.components import context_world
from pymodeler.core.entities import Entities, Entity
from pymodeler.core.model import Model
from pymodeler.core.transform import apply_point, apply_vector, inverse
from pymodeler.core.vec import normalize

if TYPE_CHECKING:
    from pymodeler.ops.draw import PlaneAxes

PARAM_TYPES = {
    "length": "a length: number in file units, unit string like \"2.4m\", or expression",
    "number": "a plain number or expression",
    "integer": "a whole number or expression",
    "angle": "an angle in degrees (or \"0.5rad\") or expression",
    "bool": "true or false (or an expression)",
    "string": "text",
    "enum": "one of a fixed set of words",
    "point": "a point [x, y, z] (z may be omitted for 0)",
    "vector": "a displacement [dx, dy, dz]",
    "direction": "an axis name like \"z\" or \"-x\", or a vector [x, y, z]",
    "points": "a list of points",
    "loops": "a list of point lists (hole outlines)",
    "size": "[x, y, z] lengths",
    "scale": "a factor, or per-axis factors [sx, sy, sz]",
    "anchor": "a point, or one of \"center\", \"min\", \"max\", \"bottom\", \"top\" of the target's box",
    "rotation": "degrees about Z, or {\"axis\": ..., \"angle\": ...}",
    "target": "the id of an earlier step (\"name\" or \"name[2]\"), a list of ids, or \"*\" for everything",
    "face": "a face selector: \"top\", \"bottom\", \"front\", \"back\", \"left\", \"right\", \"+x\"..\"-z\", "
            "\"all\", \"largest\", \"smallest\", {\"normal\": [x,y,z]}, {\"near\": [x,y,z]} or {\"index\": n}",
    "material": "a material name (declared in \"materials\", or a colour name / \"#rrggbb\")",
    "component": "the id of a component defined earlier with the \"component\" op",
    "path": "a list of points, or the id of earlier lines/arcs to follow",
    "steps": "a list of nested steps",
    "variables": "an object of variable names to values/expressions",
    "depth": "a length, or \"through\" to cut through the solid",
}
"""Parameter types with a human description (used in docs and errors)."""


@dataclass
class Param:
    """One parameter of an op."""

    name: str
    type: str
    help: str = ""
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class OpSpec:
    """Declaration of a modeling operation."""

    name: str
    category: str
    summary: str
    params: list[Param]
    run: Callable[["OpContext", dict[str, Any]], "OpOutput"]
    example: dict[str, Any] | None = None
    one_of: list[tuple[str, ...]] = field(default_factory=list)
    """Groups of alternative parameters; at least one name of each group is required."""
    grows_target: bool = False
    """Whether results are added to the target's reference (e.g. push/pull)."""
    notes: str = ""

    def param(self, name: str) -> Param | None:
        """Look a parameter up by name or alias."""
        for p in self.params:
            if p.name == name or name in p.aliases:
                return p
        return None


@dataclass
class Target:
    """Entities a step operates on, grouped into parts (one per earlier step/repeat)."""

    name: str
    parts: list[list[Entity]]

    def all(self) -> list[Entity]:
        """Every entity in every part."""
        return [e for part in self.parts for e in part]


@dataclass
class OpOutput:
    """What an op produced."""

    parts: list[list[Entity]] = field(default_factory=list)
    """Resulting entities, grouped like the target's parts where relevant."""
    names: dict[str, list[list[Entity]]] = field(default_factory=dict)
    """Additional names to register (e.g. a group's name)."""
    message: str = ""

    @staticmethod
    def single(entities: list[Entity]) -> "OpOutput":
        """Output with one part."""
        return OpOutput(parts=[list(entities)])


@dataclass
class OpContext:
    """Where an op runs: the model, the active collection and the step's frame.

    ``frame`` maps step coordinates to world coordinates (repeats translate/rotate it);
    ``world`` maps the active collection's coordinates to world coordinates.
    """

    model: Model
    entities: Entities
    frame: np.ndarray = field(default_factory=lambda: np.eye(4))
    engine: Any = None

    @property
    def world(self) -> np.ndarray:
        """Active collection to world."""
        return context_world(self.entities)

    @property
    def to_active(self) -> np.ndarray:
        """Step coordinates to active-collection coordinates."""
        return inverse(self.world) @ self.frame

    def pt(self, p: np.ndarray) -> np.ndarray:
        """A step-coordinate point in active-collection coordinates."""
        return apply_point(self.to_active, p)

    def vec(self, v: np.ndarray) -> np.ndarray:
        """A step-coordinate vector in active-collection coordinates."""
        return apply_vector(self.to_active, v)

    def world_point(self, p: np.ndarray) -> np.ndarray:
        """A step-coordinate point in world coordinates."""
        return apply_point(self.frame, p)

    def world_vec(self, v: np.ndarray) -> np.ndarray:
        """A step-coordinate vector in world coordinates."""
        return apply_vector(self.frame, v)

    def axes(self, axes: "PlaneAxes") -> "PlaneAxes":
        """Step-coordinate drawing axes expressed in the active collection."""
        u, v, n = axes
        u2, v2 = normalize(self.vec(u)), normalize(self.vec(v))
        return u2, v2, normalize(np.cross(u2, v2))


REGISTRY: dict[str, OpSpec] = {}
"""All registered ops by name (populated by :mod:`pymodeler.ops.library`)."""

ALIASES: dict[str, str] = {}
"""Alternative op names (e.g. ``delete`` for ``erase``)."""


def op(
    name: str,
    category: str,
    summary: str,
    params: list[Param],
    *,
    example: dict[str, Any] | None = None,
    one_of: list[tuple[str, ...]] | None = None,
    grows_target: bool = False,
    aliases: tuple[str, ...] = (),
    notes: str = "",
) -> Callable[[Callable[[OpContext, dict[str, Any]], OpOutput]], Callable[[OpContext, dict[str, Any]], OpOutput]]:
    """Decorator registering an op implementation."""

    def register(fn: Callable[[OpContext, dict[str, Any]], OpOutput]) -> Callable[[OpContext, dict[str, Any]], OpOutput]:
        REGISTRY[name] = OpSpec(
            name=name,
            category=category,
            summary=summary,
            params=params,
            run=fn,
            example=example,
            one_of=list(one_of or []),
            grows_target=grows_target,
            notes=notes,
        )
        for alias in aliases:
            ALIASES[alias] = name
        return fn

    return register


def get_op(name: str) -> OpSpec | None:
    """Find an op by name or alias."""
    _ensure_loaded()
    return REGISTRY.get(ALIASES.get(name, name))


def all_ops() -> list[OpSpec]:
    """Every registered op, in registration order."""
    _ensure_loaded()
    return list(REGISTRY.values())


def _ensure_loaded() -> None:
    """Import the op library so its ``@op`` declarations run."""
    if not REGISTRY:
        import pymodeler.ops.library  # noqa: F401
