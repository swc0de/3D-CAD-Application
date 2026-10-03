"""Parse what the user types into the Measurements box (VCB). No Qt dependency.

Lengths use the model's units unless they carry their own (``2500``, ``2.5m``,
``8' 6"``); several values are separated by commas (``2000,1500`` for a rectangle);
a trailing ``s`` sets a segment or side count (``24s``).
"""

from __future__ import annotations

import re

from pymodeler.core.units import UnitError, parse_angle, parse_length

_SEGMENTS = re.compile(r"^\s*(\d+)\s*s\s*$", re.IGNORECASE)


class VcbError(ValueError):
    """The typed text is not a valid value."""


def parse_segments(text: str) -> int | None:
    """``"24s"`` -> 24; ``None`` when the text is not a segment count."""
    match = _SEGMENTS.match(text)
    if not match:
        return None
    count = int(match.group(1))
    if count < 3 or count > 999:
        raise VcbError("segment count must be between 3 and 999")
    return count


def parse_lengths(text: str, units: str, count: int | None = None) -> list[float]:
    """Comma-separated lengths in millimetres.

    Raises:
        VcbError: on unparseable text or the wrong number of values.
    """
    parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
    if not parts:
        raise VcbError("type a value, e.g. 2500 or 2.5m")
    try:
        values = [parse_length(p, units) for p in parts]
    except UnitError as exc:
        raise VcbError(str(exc)) from None
    if count is not None and len(values) != count:
        raise VcbError(f"expected {count} value(s) separated by commas")
    return values


def parse_angle_text(text: str) -> float:
    """An angle in degrees (``45``, ``45deg``, ``0.5rad``)."""
    try:
        return parse_angle(text.strip())
    except UnitError as exc:
        raise VcbError(str(exc)) from None
