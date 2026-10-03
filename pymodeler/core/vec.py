"""Vector, plane and segment helpers shared by the whole geometry kernel.

All lengths are in millimetres.  ``TOL`` is the distance below which two points
are considered identical; it is deliberately small (one micron) but large enough
to absorb floating point noise from transforms and intersections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

TOL: float = 1e-3
"""Distance tolerance in millimetres."""

PARALLEL_TOL: float = 1e-9
"""Tolerance on the sine of the angle between two directions to call them parallel."""

Point = np.ndarray
Vector = np.ndarray
PointLike = Sequence[float] | np.ndarray


class GeometryError(ValueError):
    """Raised when an operation receives degenerate or invalid geometry."""


def v3(p: PointLike) -> np.ndarray:
    """Return ``p`` as a float64 numpy array of shape (3,)."""
    arr = np.asarray(p, dtype=np.float64)
    if arr.shape == (2,):
        arr = np.array([arr[0], arr[1], 0.0])
    if arr.shape != (3,):
        raise GeometryError(f"expected a 3D point, got {p!r}")
    return arr


def norm(v: Vector) -> float:
    """Euclidean length of ``v``."""
    return float(math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2))


def normalize(v: Vector) -> np.ndarray:
    """Return the unit vector in the direction of ``v``.

    Raises:
        GeometryError: if ``v`` has (near) zero length.
    """
    length = norm(v)
    if length < 1e-12:
        raise GeometryError("cannot normalise a zero-length vector")
    return np.asarray(v, dtype=np.float64) / length


def same_point(a: PointLike, b: PointLike, tol: float = TOL) -> bool:
    """True if points ``a`` and ``b`` are within ``tol`` of each other."""
    return (
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    ) <= tol * tol


def is_parallel(u: Vector, v: Vector) -> bool:
    """True if the two (non-zero) directions are parallel or anti-parallel."""
    nu, nv = norm(u), norm(v)
    if nu < 1e-12 or nv < 1e-12:
        return True
    return norm(np.cross(u, v)) / (nu * nv) < 1e-7


def newell_normal(points: Sequence[PointLike]) -> np.ndarray:
    """Un-normalised polygon normal by Newell's method (length = 2 x area)."""
    n = np.zeros(3)
    count = len(points)
    for i in range(count):
        a = points[i]
        b = points[(i + 1) % count]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


def polygon_normal(points: Sequence[PointLike]) -> np.ndarray:
    """Unit normal of a planar polygon, following the right-hand rule on its order."""
    return normalize(newell_normal(points))


def any_perpendicular(n: Vector) -> np.ndarray:
    """A unit vector perpendicular to ``n``, chosen deterministically.

    For axis-aligned normals this yields the natural axis (e.g. X for a Z normal), so
    that 2D projections of axis-aligned planes have friendly coordinates.
    """
    n = normalize(n)
    ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
    if az >= ax and az >= ay:
        helper = np.array([1.0, 0.0, 0.0])
    elif ay >= ax:
        helper = np.array([0.0, 0.0, 1.0])
    else:
        helper = np.array([0.0, 1.0, 0.0])
    u = helper - n * float(np.dot(helper, n))
    return normalize(u)


def project_point_to_segment(p: PointLike, a: PointLike, b: PointLike) -> tuple[float, float]:
    """Project ``p`` onto segment ``ab``.

    Returns:
        ``(t, distance)`` where ``t`` is the unclamped line parameter (0 at ``a``,
        1 at ``b``) and ``distance`` is the distance from ``p`` to the infinite line.
    """
    ax, ay, az = a[0], a[1], a[2]
    dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
    length2 = dx * dx + dy * dy + dz * dz
    if length2 < 1e-24:
        return 0.0, math.dist(p, a)
    px, py, pz = p[0] - ax, p[1] - ay, p[2] - az
    t = (px * dx + py * dy + pz * dz) / length2
    cx, cy, cz = ax + t * dx - p[0], ay + t * dy - p[1], az + t * dz - p[2]
    return float(t), float(math.sqrt(cx * cx + cy * cy + cz * cz))


def point_on_segment_interior(
    p: PointLike, a: PointLike, b: PointLike, tol: float = TOL
) -> bool:
    """True if ``p`` lies on segment ``ab`` strictly between its endpoints."""
    t, dist = project_point_to_segment(p, a, b)
    if dist > tol:
        return False
    length = math.dist(a, b)
    return t * length > tol and (1.0 - t) * length > tol


def segment_intersection(
    a: PointLike, b: PointLike, c: PointLike, d: PointLike, tol: float = TOL
) -> tuple[float, float, np.ndarray] | None:
    """Intersect segments ``ab`` and ``cd`` in 3D (non-parallel case only).

    Returns:
        ``(s, t, point)`` with ``s`` the parameter on ``ab`` and ``t`` on ``cd`` when the
        closest points of the two lines are within ``tol`` and both parameters fall within
        the segments (endpoints included); otherwise ``None``.  Parallel segments return
        ``None``: collinear overlaps are handled by vertex-on-segment tests instead.
    """
    a, b, c, d = v3(a), v3(b), v3(c), v3(d)
    u = b - a
    v = d - c
    w = a - c
    uu, uv, vv = float(u @ u), float(u @ v), float(v @ v)
    uw, vw = float(u @ w), float(v @ w)
    denom = uu * vv - uv * uv
    if uu < 1e-24 or vv < 1e-24 or denom <= 1e-12 * uu * vv:
        return None
    s = (uv * vw - vv * uw) / denom
    t = (uu * vw - uv * uw) / denom
    lu, lv = math.sqrt(uu), math.sqrt(vv)
    if s * lu < -tol or (s - 1.0) * lu > tol or t * lv < -tol or (t - 1.0) * lv > tol:
        return None
    pa = a + s * u
    pb = c + t * v
    if not same_point(pa, pb, tol):
        return None
    return s, t, (pa + pb) * 0.5


def bounds(points: Iterable[PointLike]) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned bounding box ``(min, max)`` of ``points``.

    Raises:
        GeometryError: if ``points`` is empty.
    """
    arr = np.array([v3(p) for p in points])
    if arr.size == 0:
        raise GeometryError("cannot compute the bounds of no points")
    return arr.min(axis=0), arr.max(axis=0)


@dataclass(frozen=True)
class Plane:
    """An oriented plane ``normal . x = offset`` with a unit normal."""

    normal: np.ndarray
    offset: float

    @staticmethod
    def from_point_normal(point: PointLike, normal: Vector) -> "Plane":
        """Plane through ``point`` with the given normal (normalised here)."""
        n = normalize(v3(normal))
        return Plane(n, float(n @ v3(point)))

    @staticmethod
    def from_points(points: Sequence[PointLike]) -> "Plane":
        """Best-fit plane of a polygon (Newell normal through the centroid).

        Raises:
            GeometryError: if the points are collinear or too few.
        """
        if len(points) < 3:
            raise GeometryError("a plane needs at least three points")
        n = newell_normal(points)
        if norm(n) < 1e-9:
            raise GeometryError("points are collinear; they do not define a plane")
        centroid = np.mean(np.array([v3(p) for p in points]), axis=0)
        return Plane.from_point_normal(centroid, n)

    @staticmethod
    def from_three_points(a: PointLike, b: PointLike, c: PointLike) -> "Plane | None":
        """Plane through three points, or ``None`` if they are (nearly) collinear."""
        a, b, c = v3(a), v3(b), v3(c)
        n = np.cross(b - a, c - a)
        scale = max(norm(b - a), norm(c - a), 1e-12)
        if norm(n) < 1e-7 * scale * scale:
            return None
        return Plane.from_point_normal(a, n)

    def distance(self, p: PointLike) -> float:
        """Signed distance from ``p`` to the plane (positive on the normal side)."""
        n = self.normal
        return float(n[0] * p[0] + n[1] * p[1] + n[2] * p[2] - self.offset)

    def contains(self, p: PointLike, tol: float = TOL) -> bool:
        """True if ``p`` lies within ``tol`` of the plane."""
        return abs(self.distance(p)) <= tol

    def flipped(self) -> "Plane":
        """The same plane with the opposite orientation."""
        return Plane(-self.normal, -self.offset)

    def canonical(self) -> "Plane":
        """The orientation of this plane whose normal points 'up' (Z, then -Y, then X).

        Two planes that coincide but face opposite ways have the same canonical form.
        """
        n = self.normal
        for axis, sign in ((2, 1.0), (1, -1.0), (0, 1.0)):
            if abs(n[axis]) > 1e-9:
                return self if n[axis] * sign > 0 else self.flipped()
        return self

    def same_plane(self, other: "Plane", tol: float = TOL) -> bool:
        """True if both planes coincide (regardless of orientation)."""
        a, b = self.canonical(), other.canonical()
        return bool(norm(a.normal - b.normal) < 1e-7 and abs(a.offset - b.offset) <= tol)

    def origin(self) -> np.ndarray:
        """The point of the plane closest to the world origin."""
        return self.normal * self.offset

    def basis(self) -> tuple[np.ndarray, np.ndarray]:
        """Orthonormal in-plane axes ``(u, v)`` with ``u x v == normal``."""
        u = any_perpendicular(self.normal)
        v = np.cross(self.normal, u)
        return u, v

    def to_2d(self, points: Iterable[PointLike]) -> list[tuple[float, float]]:
        """Project 3D points onto the plane's 2D ``(u, v)`` coordinate system."""
        u, v = self.basis()
        o = self.origin()
        out: list[tuple[float, float]] = []
        for p in points:
            d = v3(p) - o
            out.append((float(d @ u), float(d @ v)))
        return out

    def to_3d(self, uv: tuple[float, float]) -> np.ndarray:
        """Map 2D plane coordinates back to a 3D point on the plane."""
        u, v = self.basis()
        return self.origin() + u * uv[0] + v * uv[1]

    def project(self, p: PointLike) -> np.ndarray:
        """Orthogonal projection of ``p`` onto the plane."""
        p = v3(p)
        return p - self.normal * self.distance(p)


def plane_key(plane: Plane, digits: int = 4) -> tuple[float, ...]:
    """A hashable, orientation-independent key used to group coplanar items.

    Keys of nearly identical planes may still differ through rounding, so callers use
    it to de-duplicate candidates and still confirm with :meth:`Plane.same_plane`.
    """
    c = plane.canonical()
    return (
        round(float(c.normal[0]), 6),
        round(float(c.normal[1]), 6),
        round(float(c.normal[2]), 6),
        round(c.offset, digits - 1),
    )


def angle_between(u: Vector, v: Vector) -> float:
    """Angle between two vectors in degrees (0..180)."""
    nu, nv = norm(u), norm(v)
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    c = max(-1.0, min(1.0, float(np.dot(u, v)) / (nu * nv)))
    return math.degrees(math.acos(c))
