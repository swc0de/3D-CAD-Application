"""Tests for vector and plane helpers."""

import math

import numpy as np
import pytest

from pymodeler.core.vec import (
    GeometryError,
    Plane,
    angle_between,
    any_perpendicular,
    newell_normal,
    plane_key,
    point_on_segment_interior,
    polygon_normal,
    project_point_to_segment,
    same_point,
    segment_intersection,
)


def test_polygon_normal_follows_right_hand_rule() -> None:
    square = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    assert np.allclose(polygon_normal(square), [0, 0, 1])
    assert np.allclose(polygon_normal(square[::-1]), [0, 0, -1])
    assert math.isclose(np.linalg.norm(newell_normal(square)), 2.0)


def test_any_perpendicular_is_unit_and_perpendicular() -> None:
    for n in ([0, 0, 1], [1, 0, 0], [0, 1, 0], [1, 2, 3]):
        u = any_perpendicular(np.array(n, dtype=float))
        assert math.isclose(np.linalg.norm(u), 1.0)
        assert abs(np.dot(u, n)) < 1e-12


def test_plane_basis_is_right_handed() -> None:
    for n in ([0, 0, 1], [0, 0, -1], [0, -1, 0], [1, 1, 1]):
        plane = Plane.from_point_normal((1, 2, 3), np.array(n, dtype=float))
        u, v = plane.basis()
        assert np.allclose(np.cross(u, v), plane.normal)


def test_plane_projection_round_trip() -> None:
    plane = Plane.from_point_normal((0, 0, 5), (0, 0, 1))
    uv = plane.to_2d([(3, 4, 5)])[0]
    assert np.allclose(plane.to_3d(uv), (3, 4, 5))
    assert plane.contains((10, -3, 5))
    assert not plane.contains((0, 0, 5.1))
    assert math.isclose(plane.distance((0, 0, 7)), 2.0)


def test_canonical_and_same_plane() -> None:
    up = Plane.from_point_normal((0, 0, 1), (0, 0, 1))
    down = up.flipped()
    assert up.same_plane(down)
    assert plane_key(up) == plane_key(down)
    assert np.allclose(down.canonical().normal, (0, 0, 1))


def test_plane_from_collinear_points_fails() -> None:
    with pytest.raises(GeometryError):
        Plane.from_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)])
    assert Plane.from_three_points((0, 0, 0), (1, 0, 0), (2, 0, 0)) is None


def test_segment_helpers() -> None:
    t, d = project_point_to_segment((0.5, 1, 0), (0, 0, 0), (1, 0, 0))
    assert math.isclose(t, 0.5) and math.isclose(d, 1.0)
    assert point_on_segment_interior((0.5, 0, 0), (0, 0, 0), (1, 0, 0))
    assert not point_on_segment_interior((0, 0, 0), (0, 0, 0), (1, 0, 0))
    hit = segment_intersection((0, 0, 0), (2, 0, 0), (1, -1, 0), (1, 1, 0))
    assert hit is not None and np.allclose(hit[2], (1, 0, 0))
    # Skew segments do not intersect.
    assert segment_intersection((0, 0, 0), (2, 0, 0), (1, -1, 1), (1, 1, 1)) is None
    # Parallel segments are reported as no intersection.
    assert segment_intersection((0, 0, 0), (2, 0, 0), (0, 1, 0), (2, 1, 0)) is None


def test_same_point_and_angle() -> None:
    assert same_point((0, 0, 0), (0, 0, 0.0005))
    assert not same_point((0, 0, 0), (0, 0, 0.01))
    assert math.isclose(angle_between(np.array([1, 0, 0]), np.array([0, 1, 0])), 90.0)
