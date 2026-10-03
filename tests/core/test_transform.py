"""Tests for 4x4 transforms."""

import numpy as np
import pytest

from pymodeler.core.transform import (
    apply_normal,
    apply_point,
    apply_points,
    compose,
    inverse,
    is_identity,
    is_mirroring,
    rotation,
    scaling,
    translation,
)
from pymodeler.core.vec import GeometryError


def test_translation_and_inverse() -> None:
    m = translation((1, 2, 3))
    assert np.allclose(apply_point(m, (0, 0, 0)), (1, 2, 3))
    assert is_identity(compose(m, inverse(m)))


def test_rotation_about_center() -> None:
    m = rotation((0, 0, 1), 90, center=(1, 0, 0))
    assert np.allclose(apply_point(m, (2, 0, 0)), (1, 1, 0))
    assert np.allclose(apply_point(m, (1, 0, 5)), (1, 0, 5))


def test_scaling_about_center_and_mirroring() -> None:
    m = scaling((2, 1, 1), center=(1, 0, 0))
    assert np.allclose(apply_points(m, [(2, 0, 0), (0, 0, 0)]), [(3, 0, 0), (-1, 0, 0)])
    assert not is_mirroring(m)
    assert is_mirroring(scaling((-1, 1, 1)))
    with pytest.raises(GeometryError):
        scaling(0)


def test_compose_order() -> None:
    # compose(A, B) applies B first.
    m = compose(translation((10, 0, 0)), rotation((0, 0, 1), 90))
    assert np.allclose(apply_point(m, (1, 0, 0)), (10, 1, 0))


def test_normals_under_non_uniform_scale() -> None:
    m = scaling((2, 1, 1))
    n = apply_normal(m, np.array([1.0, 1.0, 0.0]) / np.sqrt(2))
    assert np.isclose(np.linalg.norm(n), 1.0)
    # The normal must stay perpendicular to a transformed in-plane direction.
    d = m[:3, :3] @ np.array([1.0, -1.0, 0.0])
    assert abs(np.dot(n, d)) < 1e-12
