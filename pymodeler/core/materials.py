"""Materials (colour + opacity) and colour parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

Color = tuple[float, float, float]

DEFAULT_FRONT_COLOR: Color = (0.96, 0.96, 0.94)
"""Colour of unpainted front faces (SketchUp's off-white)."""

DEFAULT_BACK_COLOR: Color = (0.64, 0.69, 0.75)
"""Colour of unpainted back faces (SketchUp's blue-grey)."""

NAMED_COLORS: dict[str, str] = {
    "black": "#000000", "white": "#ffffff", "gray": "#808080", "grey": "#808080",
    "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "darkgray": "#505050", "darkgrey": "#505050",
    "silver": "#c0c0c0", "red": "#d03030", "darkred": "#8b0000", "green": "#3a9a3a",
    "darkgreen": "#1f5f1f", "lime": "#00ff00", "blue": "#3060d0", "navy": "#000080",
    "skyblue": "#87ceeb", "lightblue": "#add8e6", "yellow": "#f2d32b", "gold": "#d4af37",
    "orange": "#f08c1e", "brown": "#8b5a2b", "tan": "#d2b48c", "beige": "#e8dcc0",
    "wood": "#b07d4f", "oak": "#c19a6b", "walnut": "#5d432c", "pine": "#e3c08d",
    "brick": "#a0522d", "concrete": "#a9a9a0", "stone": "#8e8e86", "glass": "#88ccff",
    "steel": "#8c9aa3", "aluminium": "#c8cdd0", "aluminum": "#c8cdd0", "copper": "#b87333",
    "terracotta": "#c46a43", "slate": "#5a6470", "grass": "#5d8c3a", "water": "#3f86c6",
    "purple": "#7a3fa0", "pink": "#f0a0c0", "cyan": "#30c0d0", "magenta": "#d030a0",
    "olive": "#808000", "teal": "#008080", "maroon": "#800000", "ivory": "#fffff0",
}
"""Colour names accepted wherever a colour is expected."""


class ColorError(ValueError):
    """Raised when a colour value cannot be parsed."""


def parse_color(value: Any) -> tuple[Color, float | None]:
    """Parse a colour into ``((r, g, b), alpha)`` with components in 0..1.

    Accepted forms: ``"#rgb"``, ``"#rrggbb"``, ``"#rrggbbaa"``, a colour name, or a list
    of 3/4 numbers (either all within 0..1, or 0..255).  ``alpha`` is ``None`` unless the
    value specifies one.

    Raises:
        ColorError: if the value is not a recognised colour.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text in NAMED_COLORS:
            text = NAMED_COLORS[text]
        if not text.startswith("#"):
            raise ColorError(f"unknown colour {value!r} (use '#rrggbb', a name, or [r, g, b])")
        digits = text[1:]
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        if len(digits) not in (6, 8):
            raise ColorError(f"bad hex colour {value!r}")
        try:
            comps = [int(digits[i:i + 2], 16) / 255.0 for i in range(0, len(digits), 2)]
        except ValueError as exc:
            raise ColorError(f"bad hex colour {value!r}") from exc
        alpha = comps[3] if len(comps) == 4 else None
        return (comps[0], comps[1], comps[2]), alpha
    if isinstance(value, Sequence) and len(value) in (3, 4):
        if not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in value):
            raise ColorError(f"colour components must be numbers, got {value!r}")
        scale = 255.0 if any(c > 1.0 for c in value[:3]) else 1.0
        rgb = tuple(min(1.0, max(0.0, float(c) / scale)) for c in value[:3])
        alpha = None
        if len(value) == 4:
            a = float(value[3])
            alpha = min(1.0, max(0.0, a / 255.0 if a > 1.0 else a))
        return (rgb[0], rgb[1], rgb[2]), alpha
    raise ColorError(f"cannot parse colour {value!r}")


def color_to_hex(color: Color) -> str:
    """Format an RGB colour (0..1 components) as ``#rrggbb``."""
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in color)


@dataclass
class Material:
    """A named surface appearance."""

    name: str
    color: Color = DEFAULT_FRONT_COLOR
    opacity: float = 1.0

    @staticmethod
    def from_spec(name: str, spec: Any) -> "Material":
        """Build a material from a colour value or a ``{"color", "opacity"}`` dict.

        Raises:
            ColorError: on an invalid colour or opacity.
        """
        if isinstance(spec, dict):
            color, alpha = parse_color(spec.get("color", "#ffffff"))
            opacity = spec.get("opacity", alpha if alpha is not None else 1.0)
        else:
            color, alpha = parse_color(spec)
            opacity = alpha if alpha is not None else 1.0
        if not isinstance(opacity, (int, float)) or not 0.0 <= float(opacity) <= 1.0:
            raise ColorError(f"material {name!r}: opacity must be between 0 and 1")
        return Material(name=name, color=color, opacity=float(opacity))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {"color": color_to_hex(self.color), "opacity": self.opacity}

    @property
    def is_transparent(self) -> bool:
        """True if the material is not fully opaque."""
        return self.opacity < 0.999
