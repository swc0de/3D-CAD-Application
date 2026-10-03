"""Tests for camera navigation math."""

import math

import numpy as np
import pytest

from pymodeler.render.camera import Camera
from pymodeler.ui.navigation import (
    orbit,
    pan,
    pixel_ray,
    pixel_to_target_plane,
    set_perspective,
    wheel_factor,
    zoom,
)

W, H = 800, 600


def iso() -> Camera:
    return Camera.standard("iso", (np.zeros(3), np.full(3, 1000.0)), W / H)


def elevation(cam: Camera) -> float:
    return math.degrees(math.asin(cam.basis()[2][2]))


def test_center_pixel_ray_hits_target() -> None:
    cam = iso()
    assert np.allclose(pixel_to_target_plane(cam, W / 2, H / 2, W, H), cam.target)
    origin, direction = pixel_ray(cam, W / 2, H / 2, W, H)
    assert np.allclose(direction, -cam.basis()[2])


def test_orbit_keeps_distance_and_clamps_pitch() -> None:
    cam = iso()
    d = cam.distance()
    orbit(cam, 100, 0)
    assert cam.distance() == pytest.approx(d)
    before = elevation(cam)
    orbit(cam, 0, 20)
    assert elevation(cam) > before, "dragging down raises the eye"
    orbit(cam, 0, 10000)
    assert elevation(cam) <= 89.0 + 1e-6
    orbit(cam, 0, -20000)
    assert elevation(cam) >= -89.0 - 1e-6
    assert cam.distance() == pytest.approx(d)


def test_pan_moves_eye_and_target_together() -> None:
    cam = iso()
    offset = cam.eye - cam.target
    point = pixel_to_target_plane(cam, 100, 100, W, H)
    pan(cam, 50, 30, H)
    assert np.allclose(cam.eye - cam.target, offset)
    assert np.allclose(pixel_to_target_plane(cam, 150, 130, W, H), point), "the grabbed point follows the mouse"


@pytest.mark.parametrize("perspective", [True, False])
def test_zoom_keeps_point_under_cursor(perspective: bool) -> None:
    cam = iso()
    set_perspective(cam, perspective)
    point = pixel_to_target_plane(cam, 200, 150, W, H)
    zoom(cam, 0.5, 200, 150, W, H)
    assert np.allclose(pixel_to_target_plane(cam, 200, 150, W, H), point, atol=1e-6)


def test_zoom_in_reduces_distance_or_height() -> None:
    cam = iso()
    d = cam.distance()
    zoom(cam, wheel_factor(1), W / 2, H / 2, W, H)
    assert cam.distance() < d
    set_perspective(cam, False)
    h = cam.ortho_height
    zoom(cam, wheel_factor(2), W / 2, H / 2, W, H)
    assert cam.ortho_height == pytest.approx(h * 0.85**2)


def test_projection_toggle_preserves_scale() -> None:
    cam = iso()
    set_perspective(cam, False)
    h = cam.ortho_height
    set_perspective(cam, True)
    set_perspective(cam, False)
    assert cam.ortho_height == pytest.approx(h)
