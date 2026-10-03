"""Tests for the drawing operations."""

import math

import numpy as np
import pytest

from pymodeler.core.entities import Entities
from pymodeler.core.vec import GeometryError
from pymodeler.ops import draw


def test_plane_axes_are_right_handed() -> None:
    for name in ("xy", "xz", "yz"):
        u, v, n = draw.plane_axes(name)
        assert np.allclose(np.cross(u, v), n)
    u, v, n = draw.plane_axes(normal=(0, 0, -1))
    assert np.allclose(np.cross(u, v), n) and np.allclose(u, (1, 0, 0))
    u, v, n = draw.plane_axes(normal=(1, 1, 0), x_axis=(0, 0, 1))
    assert np.allclose(u, (0, 0, 1))
    with pytest.raises(GeometryError):
        draw.plane_axes("ab")


def test_rectangle_in_each_plane() -> None:
    ents = Entities()
    r = draw.rectangle(ents, (0, 0, 0), 1000, 500)
    assert len(r.faces) == 1 and len(r.edges) == 4
    assert np.allclose(r.faces[0].normal, (0, 0, 1))
    wall = draw.rectangle(Entities(), (0, 0, 0), 1000, 2000, draw.plane_axes("xz"))
    assert np.allclose(wall.faces[0].normal, (0, -1, 0))
    side = draw.rectangle(Entities(), (0, 0, 0), 1000, 2000, draw.plane_axes("yz"))
    assert np.allclose(side.faces[0].normal, (1, 0, 0))


def test_rectangle_centered_and_negative_sizes_keep_normal() -> None:
    r = draw.rectangle(Entities(), (0, 0, 0), 1000, 500, centered=True)
    assert np.allclose(r.faces[0].centroid(), (0, 0, 0))
    neg = draw.rectangle(Entities(), (0, 0, 0), -1000, 500)
    assert np.allclose(neg.faces[0].normal, (0, 0, 1))
    with pytest.raises(GeometryError):
        draw.rectangle(Entities(), (0, 0, 0), 0, 500)


def test_circle_is_one_curve() -> None:
    c = draw.circle(Entities(), (0, 0, 0), 500, 24)
    assert len(c.faces) == 1 and len(c.edges) == 24
    assert len({e.curve for e in c.edges}) == 1 and c.edges[0].curve is not None
    expected = 0.5 * 24 * 500**2 * math.sin(2 * math.pi / 24)
    assert c.faces[0].area() == pytest.approx(expected)
    with pytest.raises(GeometryError):
        draw.circle(Entities(), (0, 0, 0), -1, 24)


def test_polygon_inscribed_and_circumscribed() -> None:
    ins = draw.polygon(Entities(), (0, 0, 0), 500, 4)
    circ = draw.polygon(Entities(), (0, 0, 0), 500, 4, inscribed=False)
    assert ins.faces[0].area() == pytest.approx(500_000)
    assert circ.faces[0].area() == pytest.approx(1_000_000)


def test_arc_variants() -> None:
    ents = Entities()
    a = draw.arc(ents, (0, 0, 0), 500, 0, 90, 6)
    assert not a.faces and len(a.edges) == 6
    chord = draw.arc(Entities(), (0, 0, 0), 500, 0, 180, 12, close="chord")
    assert len(chord.faces) == 1
    pie = draw.arc(Entities(), (0, 0, 0), 500, 0, 90, 12, close="pie")
    assert len(pie.faces) == 1
    with pytest.raises(GeometryError):
        draw.arc(Entities(), (0, 0, 0), 500, 10, 10)


def test_line_and_face() -> None:
    ents = Entities()
    loop = draw.line(ents, [(0, 0, 0), (1000, 0, 0), (1000, 1000, 0), (0, 1000, 0)], closed=True)
    assert len(loop.faces) == 1
    with pytest.raises(GeometryError):
        draw.line(Entities(), [(0, 0, 0)])
    f = draw.face(Entities(), [(0, 0, 0), (1000, 0, 0), (0, 1000, 0)], material="red")
    assert f.faces[0].material == "red" and len(f.edges) == 3
