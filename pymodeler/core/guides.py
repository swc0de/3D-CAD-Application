"""Construction guides: infinite dashed lines and guide points (made by the Tape Measure).

Guides help with drawing (the cursor snaps to them) but are not geometry: they never
stick to edges or faces, and are not exported. They live in world coordinates.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from pymodeler.core.vec import GeometryError, PointLike, norm, v3


class Guide:
    """A guide line through ``point`` along ``direction``, or a guide point."""

    def __init__(self, point: PointLike, direction: PointLike | None = None) -> None:
        self.point = v3(point)
        self.direction: np.ndarray | None = None
        if direction is not None:
            d = v3(direction)
            length = norm(d)
            if length < 1e-9:
                raise GeometryError("a guide line needs a non-zero direction")
            self.direction = d / length

    def __repr__(self) -> str:
        kind = "point" if self.direction is None else f"line along {np.round(self.direction, 3).tolist()}"
        return f"<Guide {kind} at {np.round(self.point, 3).tolist()}>"

    @property
    def is_point(self) -> bool:
        """True for a guide point, False for a guide line."""
        return self.direction is None

    def closest_point(self, p: PointLike) -> np.ndarray:
        """The point of the guide nearest ``p``."""
        if self.direction is None:
            return self.point.copy()
        return self.point + self.direction * float((v3(p) - self.point) @ self.direction)

    def distance(self, p: PointLike) -> float:
        """Distance from ``p`` to the guide."""
        return norm(v3(p) - self.closest_point(p))

    def transformed(self, matrix: np.ndarray) -> "Guide":
        """A copy moved by a 4x4 transform."""
        point = matrix[:3, :3] @ self.point + matrix[:3, 3]
        direction = None if self.direction is None else matrix[:3, :3] @ self.direction
        return Guide(point, direction)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"point": [round(float(c), 6) for c in self.point]}
        if self.direction is not None:
            data["direction"] = [round(float(c), 9) for c in self.direction]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Guide":
        return cls(data["point"], data.get("direction"))
