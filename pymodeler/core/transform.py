"""4x4 homogeneous transforms (column-vector convention: ``p' = M @ p``)."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from pymodeler.core.vec import GeometryError, PointLike, normalize, v3

Matrix = np.ndarray


def identity() -> np.ndarray:
    """The 4x4 identity matrix."""
    return np.eye(4)


def translation(offset: PointLike) -> np.ndarray:
    """Matrix translating by ``offset``."""
    m = np.eye(4)
    m[:3, 3] = v3(offset)
    return m


def scaling(factors: float | PointLike, center: PointLike = (0.0, 0.0, 0.0)) -> np.ndarray:
    """Matrix scaling by ``factors`` (uniform float or per-axis triple) about ``center``.

    Raises:
        GeometryError: if any factor is zero (the result would be degenerate).
    """
    if isinstance(factors, (int, float)):
        f = np.array([float(factors)] * 3)
    else:
        f = v3(factors)
    if np.any(np.abs(f) < 1e-12):
        raise GeometryError("scale factors must be non-zero")
    c = v3(center)
    m = np.diag([f[0], f[1], f[2], 1.0])
    return translation(c) @ m @ translation(-c)


def rotation(
    axis: PointLike, angle_deg: float, center: PointLike = (0.0, 0.0, 0.0)
) -> np.ndarray:
    """Matrix rotating ``angle_deg`` degrees about ``axis`` through ``center``.

    Positive angles turn counter-clockwise when looking down the axis toward its origin
    (right-hand rule).
    """
    k = normalize(v3(axis))
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = k
    r = np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
        ]
    )
    m = np.eye(4)
    m[:3, :3] = r
    cen = v3(center)
    return translation(cen) @ m @ translation(-cen)


def from_axes(
    origin: PointLike, x_axis: PointLike, y_axis: PointLike, z_axis: PointLike
) -> np.ndarray:
    """Matrix mapping the world axes onto the given axes placed at ``origin``."""
    m = np.eye(4)
    m[:3, 0] = v3(x_axis)
    m[:3, 1] = v3(y_axis)
    m[:3, 2] = v3(z_axis)
    m[:3, 3] = v3(origin)
    return m


def compose(*matrices: np.ndarray) -> np.ndarray:
    """Compose transforms; ``compose(A, B)`` applies ``B`` first, then ``A``."""
    out = np.eye(4)
    for m in matrices:
        out = out @ m
    return out


def inverse(m: np.ndarray) -> np.ndarray:
    """Inverse of a transform.

    Raises:
        GeometryError: if the matrix is singular.
    """
    try:
        return np.linalg.inv(m)
    except np.linalg.LinAlgError as exc:
        raise GeometryError("transform is not invertible") from exc


def apply_point(m: np.ndarray, p: PointLike) -> np.ndarray:
    """Transform a single point."""
    p = v3(p)
    return m[:3, :3] @ p + m[:3, 3]


def apply_points(m: np.ndarray, points: Sequence[PointLike] | np.ndarray) -> np.ndarray:
    """Transform an (N, 3) array of points."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    return pts @ m[:3, :3].T + m[:3, 3]


def apply_vector(m: np.ndarray, v: PointLike) -> np.ndarray:
    """Transform a direction (ignores translation)."""
    return m[:3, :3] @ v3(v)


def apply_normal(m: np.ndarray, n: PointLike) -> np.ndarray:
    """Transform a surface normal (inverse transpose), returning a unit vector."""
    inv_t = np.linalg.inv(m[:3, :3]).T
    return normalize(inv_t @ v3(n))


def is_identity(m: np.ndarray, tol: float = 1e-12) -> bool:
    """True if ``m`` is (numerically) the identity."""
    return bool(np.allclose(m, np.eye(4), atol=tol))


def is_mirroring(m: np.ndarray) -> bool:
    """True if the transform flips handedness (negative determinant).

    Mirrored instances must reverse face winding to keep normals pointing outward.
    """
    return float(np.linalg.det(m[:3, :3])) < 0.0


def origin_of(m: np.ndarray) -> np.ndarray:
    """The translation part of a transform."""
    return m[:3, 3].copy()
