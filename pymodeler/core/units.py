"""Length and angle units.

The kernel always works in millimetres.  This module converts user-facing values
(``"2.4m"``, ``"300 mm"``, ``"8ft"``, ``8' 6"``) to millimetres and formats
millimetres back for display in a chosen unit.
"""

from __future__ import annotations

import math
import re

MM_PER_UNIT: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
}
"""Millimetres per unit for each canonical length unit."""

_UNIT_ALIASES: dict[str, str] = {
    "mm": "mm", "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "in": "in", "inch": "in", "inches": "in", '"': "in",
    "ft": "ft", "foot": "ft", "feet": "ft", "'": "ft",
}

ANGLE_UNITS: dict[str, float] = {"deg": 1.0, "rad": 180.0 / math.pi}
"""Degrees per unit for each angle unit."""

_ANGLE_ALIASES: dict[str, str] = {
    "deg": "deg", "degree": "deg", "degrees": "deg", "°": "deg",
    "rad": "rad", "radian": "rad", "radians": "rad",
}

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_VALUE_RE = re.compile(rf"^\s*({_NUMBER})\s*([A-Za-z°\"']*)\s*$")
_FEET_INCHES_RE = re.compile(rf"^\s*({_NUMBER})\s*'\s*-?\s*({_NUMBER})\s*\"?\s*$")


class UnitError(ValueError):
    """Raised for unknown units or unparseable values."""


def normalize_unit(unit: str) -> str:
    """Return the canonical name of a length unit (``"meters"`` -> ``"m"``).

    Raises:
        UnitError: if the unit is unknown.
    """
    key = unit.strip().lower() if unit not in ('"', "'") else unit
    if key not in _UNIT_ALIASES:
        known = ", ".join(MM_PER_UNIT)
        raise UnitError(f"unknown length unit {unit!r} (use one of: {known})")
    return _UNIT_ALIASES[key]


def is_length_unit(unit: str) -> bool:
    """True if ``unit`` names a length unit."""
    try:
        normalize_unit(unit)
    except UnitError:
        return False
    return True


def is_angle_unit(unit: str) -> bool:
    """True if ``unit`` names an angle unit."""
    return unit.strip().lower() in _ANGLE_ALIASES


def to_mm(value: float, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` to millimetres."""
    return float(value) * MM_PER_UNIT[normalize_unit(unit)]


def from_mm(value_mm: float, unit: str) -> float:
    """Convert millimetres to ``unit``."""
    return float(value_mm) / MM_PER_UNIT[normalize_unit(unit)]


def parse_length(text: str | float | int, default_unit: str = "mm") -> float:
    """Parse a length such as ``"2.4m"``, ``"300 mm"``, ``"8ft"`` or ``8' 6"``.

    Bare numbers (or numeric strings without a unit) use ``default_unit``.

    Returns:
        The length in millimetres.

    Raises:
        UnitError: if the text is not a valid length.
    """
    if isinstance(text, bool):
        raise UnitError(f"expected a length, got {text!r}")
    if isinstance(text, (int, float)):
        return to_mm(float(text), default_unit)
    feet_inches = _FEET_INCHES_RE.match(text)
    if feet_inches:
        feet = float(feet_inches.group(1))
        inches = float(feet_inches.group(2))
        sign = -1.0 if feet < 0 or feet_inches.group(1).startswith("-") else 1.0
        return sign * (abs(feet) * MM_PER_UNIT["ft"] + inches * MM_PER_UNIT["in"])
    match = _VALUE_RE.match(text)
    if not match:
        raise UnitError(f"cannot parse length {text!r} (expected e.g. '2.4m', '300mm', '8ft')")
    number = float(match.group(1))
    unit = match.group(2) or default_unit
    return to_mm(number, unit)


def parse_angle(text: str | float | int, default_unit: str = "deg") -> float:
    """Parse an angle such as ``"45deg"``, ``"0.5rad"`` or ``90``.

    Returns:
        The angle in degrees.

    Raises:
        UnitError: if the text is not a valid angle.
    """
    if isinstance(text, bool):
        raise UnitError(f"expected an angle, got {text!r}")
    if isinstance(text, (int, float)):
        return float(text) * ANGLE_UNITS[_ANGLE_ALIASES[default_unit]]
    match = _VALUE_RE.match(text)
    if not match:
        raise UnitError(f"cannot parse angle {text!r} (expected e.g. '45deg' or '0.5rad')")
    unit = (match.group(2) or default_unit).lower()
    if unit not in _ANGLE_ALIASES:
        raise UnitError(f"unknown angle unit {unit!r} (use deg or rad)")
    return float(match.group(1)) * ANGLE_UNITS[_ANGLE_ALIASES[unit]]


def format_length(value_mm: float, unit: str = "mm", precision: int = 2) -> str:
    """Format a millimetre length for display in ``unit``.

    Feet use the architectural ``8' 6"`` style; other units print a trimmed decimal
    followed by the unit symbol, e.g. ``"2.5m"`` or ``"300mm"``.
    """
    unit = normalize_unit(unit)
    if unit == "ft":
        total_in = value_mm / MM_PER_UNIT["in"]
        sign = "-" if total_in < 0 else ""
        total_in = abs(total_in)
        feet = int(total_in // 12)
        inches = round(total_in - feet * 12, precision)
        if inches >= 12:
            feet, inches = feet + 1, 0.0
        return f"{sign}{feet}' {_trim(inches, precision)}\""
    if unit == "in":
        return f"{_trim(from_mm(value_mm, unit), precision)}\""
    return f"{_trim(from_mm(value_mm, unit), precision)}{unit}"


def _trim(value: float, precision: int) -> str:
    """Format ``value`` with at most ``precision`` decimals, dropping trailing zeros."""
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text
