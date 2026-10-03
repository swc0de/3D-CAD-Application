"""Tags (SketchUp's layers): named visibility switches for entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pymodeler.core.materials import Color, color_to_hex, parse_color

UNTAGGED = "Untagged"
"""Name of the default tag every entity belongs to."""


@dataclass
class Tag:
    """A named visibility group."""

    name: str
    visible: bool = True
    color: Color = field(default=(0.5, 0.5, 0.5))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {"visible": self.visible, "color": color_to_hex(self.color)}

    @staticmethod
    def from_spec(name: str, spec: Any) -> "Tag":
        """Build a tag from ``{"visible": bool, "color": ...}`` (or ``None``)."""
        if spec is None or spec == {}:
            return Tag(name)
        if not isinstance(spec, dict):
            raise ValueError(f"tag {name!r}: expected an object, got {spec!r}")
        visible = bool(spec.get("visible", True))
        color = parse_color(spec["color"])[0] if "color" in spec else (0.5, 0.5, 0.5)
        return Tag(name=name, visible=visible, color=color)
