"""Unit and axis conversions shared by the exporters and importers."""

from __future__ import annotations

import numpy as np

from pymodeler.core.units import MM_PER_UNIT, normalize_unit


def z_up_to_y_up(points: np.ndarray) -> np.ndarray:
    """Convert (x, y, z) Z-up coordinates to Y-up: (x, z, -y)."""
    out = np.empty_like(points)
    out[:, 0] = points[:, 0]
    out[:, 1] = points[:, 2]
    out[:, 2] = -points[:, 1]
    return out


def y_up_to_z_up(points: np.ndarray) -> np.ndarray:
    """Convert (x, y, z) Y-up coordinates to Z-up: (x, -z, y)."""
    out = np.empty_like(points)
    out[:, 0] = points[:, 0]
    out[:, 1] = -points[:, 2]
    out[:, 2] = points[:, 1]
    return out


def mm_scale(unit: str) -> float:
    """Factor converting millimetres to ``unit``."""
    return 1.0 / MM_PER_UNIT[normalize_unit(unit)]


def srgb_to_linear(c: float) -> float:
    """sRGB channel (0..1) to linear light, as glTF colour factors expect."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
