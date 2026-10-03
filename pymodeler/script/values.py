"""Convert JSON values in build scripts into typed operation arguments.

Numbers are in the file's units unless they carry their own (``"300mm"``); strings are
expressions (see :mod:`pymodeler.script.expr`).  Lengths come out in millimetres,
angles in degrees, points as numpy arrays.
"""

from __future__ import annotations

import difflib
from typing import Any, Callable

import numpy as np

from pymodeler.core.vec import GeometryError, normalize
from pymodeler.ops.registry import Param
from pymodeler.ops.selectors import DIRECTIONS
from pymodeler.script.expr import Env, ExprError, evaluate

AXES: dict[str, tuple[float, float, float]] = {
    "x": (1, 0, 0), "+x": (1, 0, 0), "-x": (-1, 0, 0),
    "y": (0, 1, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
    "z": (0, 0, 1), "+z": (0, 0, 1), "-z": (0, 0, -1),
    "up": (0, 0, 1), "down": (0, 0, -1),
}
"""Axis names accepted wherever a direction is expected."""

ANCHORS = ("center", "min", "max", "bottom", "top")


class ValueReader:
    """Reads parameter values using the current variables, units and references."""

    def __init__(
        self,
        env: Env,
        mm_per_unit: float,
        resolve_target: Callable[[Any], Any],
        resolve_component: Callable[[str], Any],
        resolve_material: Callable[[str | None], str | None],
    ) -> None:
        self.env = env
        self.mm = mm_per_unit
        self.resolve_target = resolve_target
        self.resolve_component = resolve_component
        self.resolve_material = resolve_material

    # -- scalars ------------------------------------------------------------------------
    def value(self, raw: Any) -> Any:
        """Evaluate a JSON scalar: numbers pass through, strings are expressions."""
        if isinstance(raw, bool):
            return 1.0 if raw else 0.0
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            return evaluate(raw, self.env)
        if isinstance(raw, list):
            return [self.value(x) for x in raw]
        raise ExprError(f"expected a number or expression, got {raw!r}")

    def number(self, raw: Any) -> float:
        """A plain number (file units for lengths are not converted here)."""
        v = self.value(raw)
        if isinstance(v, list):
            raise ExprError(f"expected a single number, got a list {raw!r}")
        return float(v)

    def length(self, raw: Any) -> float:
        """A length in millimetres."""
        return round(self.number(raw) * self.mm, 6)

    def integer(self, raw: Any) -> int:
        """A whole number."""
        v = self.number(raw)
        if abs(v - round(v)) > 1e-9:
            raise ExprError(f"expected a whole number, got {v:g}")
        return int(round(v))

    def boolean(self, raw: Any) -> bool:
        """true/false, or an expression that is non-zero."""
        if isinstance(raw, bool):
            return raw
        return bool(self.number(raw))

    # -- vectors --------------------------------------------------------------------------
    def coords(self, raw: Any) -> list[float]:
        """Two or three numbers (file units) from a list or a list-valued expression."""
        values = self.value(raw) if isinstance(raw, str) else [self.number(c) for c in raw] if isinstance(raw, list) else None
        if not isinstance(values, list) or len(values) not in (2, 3):
            raise ExprError(f"expected [x, y, z], got {raw!r}")
        nums = [float(c) for c in values]
        return nums + [0.0] if len(nums) == 2 else nums

    def point(self, raw: Any) -> np.ndarray:
        """A point in millimetres."""
        return np.round(np.array(self.coords(raw)) * self.mm, 6)

    def direction(self, raw: Any) -> np.ndarray:
        """A unit vector from an axis name or a vector."""
        if isinstance(raw, str) and raw.strip().lower() in AXES:
            return np.array(AXES[raw.strip().lower()], dtype=float)
        try:
            return normalize(np.array(self.coords(raw)))
        except GeometryError:
            raise ExprError("a direction cannot be the zero vector") from None

    def scale(self, raw: Any) -> float | np.ndarray:
        """A uniform factor or per-axis factors (unitless)."""
        if isinstance(raw, list):
            factors = [self.number(c) for c in raw]
            if len(factors) != 3:
                raise ExprError("per-axis scale needs [sx, sy, sz]")
            out: float | np.ndarray = np.array(factors)
        else:
            out = self.number(raw)
        if np.any(np.abs(np.atleast_1d(out)) < 1e-12):
            raise ExprError("scale factors must be non-zero")
        return out

    # -- dispatch ---------------------------------------------------------------------------
    def read(self, param: Param, raw: Any) -> Any:
        """Convert ``raw`` according to ``param.type``."""
        kind = param.type
        if raw is None and kind not in ("material",):
            return None
        if kind == "length":
            return self.length(raw)
        if kind == "number":
            return self.number(raw)
        if kind == "integer":
            return self.integer(raw)
        if kind == "angle":
            return self.number(raw)
        if kind == "bool":
            return self.boolean(raw)
        if kind == "string":
            return str(raw)
        if kind == "enum":
            return self._enum(param, raw)
        if kind in ("point", "vector"):
            return self.point(raw)
        if kind == "size":
            if isinstance(raw, list):
                return self.point(raw)
            side = self.length(raw)
            return np.array([side, side, side])
        if kind == "direction":
            return self.direction(raw)
        if kind == "points":
            if not isinstance(raw, list):
                raise ExprError("expected a list of points")
            return [self.point(p) for p in raw]
        if kind == "loops":
            return [[self.point(p) for p in loop] for loop in raw]
        if kind == "scale":
            return self.scale(raw)
        if kind == "anchor":
            if isinstance(raw, str) and raw.strip().lower() in ANCHORS:
                return raw.strip().lower()
            return self.point(raw)
        if kind == "rotation":
            if isinstance(raw, dict):
                return self.direction(raw.get("axis", "z")), self.number(raw.get("angle", 0))
            return np.array([0.0, 0.0, 1.0]), self.number(raw)
        if kind == "depth":
            if isinstance(raw, str) and raw.strip().lower() == "through":
                return "through"
            return self.length(raw)
        if kind == "face":
            return self._face(raw)
        if kind == "target":
            return self.resolve_target(raw)
        if kind == "path":
            if isinstance(raw, list) and raw and isinstance(raw[0], list):
                return [self.point(p) for p in raw]
            return self.resolve_target(raw)
        if kind == "component":
            return self.resolve_component(str(raw))
        if kind == "material":
            return self.resolve_material(raw)
        if kind in ("steps", "variables"):
            return raw
        raise ExprError(f"unsupported parameter type {kind!r}")  # pragma: no cover

    def _enum(self, param: Param, raw: Any) -> str:
        """A word from ``param.choices``."""
        text = str(raw).strip().lower()
        choices = param.choices or ()
        if text not in choices:
            hint = difflib.get_close_matches(text, choices, 1)
            extra = f" (did you mean {hint[0]!r}?)" if hint else ""
            raise ExprError(f"must be one of {', '.join(choices)}; got {raw!r}{extra}")
        return text

    def _face(self, raw: Any) -> Any:
        """A face selector with any points/vectors converted."""
        if isinstance(raw, str):
            key = raw.strip().lower()
            if key not in DIRECTIONS and key not in ("all", "largest", "smallest"):
                options = list(DIRECTIONS) + ["all", "largest", "smallest"]
                hint = difflib.get_close_matches(key, options, 1)
                extra = f" (did you mean {hint[0]!r}?)" if hint else ""
                raise ExprError(f"unknown face selector {raw!r}{extra}")
            return key
        if isinstance(raw, dict):
            if "normal" in raw:
                return {"normal": self.direction(raw["normal"])}
            if "near" in raw:
                return {"near": self.point(raw["near"])}
            if "index" in raw:
                return {"index": self.integer(raw["index"])}
        raise ExprError(f"invalid face selector {raw!r}")
