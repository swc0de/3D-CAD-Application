"""The Follow Me tool and the Intersect Faces commands."""

import numpy as np
import pytest

from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.core.entities import Face
from pymodeler.ops import draw, primitives
from pymodeler.ops.organize import make_group

from .test_draw_tools import click, hover


def build(window, fn):
    return window.document.perform("Setup", lambda: fn(window.document.model.entities))


def ents(window):
    return window.document.model.entities


@pytest.fixture
def scene(window):
    """An L-shaped path of lines with a square profile at its start."""
    path = build(window, lambda e: draw.line(e, [(0, 0, 0), (1000, 0, 0), (1000, 1000, 0)]).edges)
    build(window, lambda e: draw.face(e, [(0, -50, -50), (0, 50, -50), (0, 50, 50), (0, -50, 50)]))
    window.viewport.set_view("iso")
    window.viewport.zoom_extents()
    return window, path


def profile_of(window) -> Face:
    return next(f for f in ents(window).faces.values() if abs(f.normal[0]) > 0.99)


def test_follow_me_along_a_selected_path(scene) -> None:
    window, path = scene
    window.viewport.selection.set(path)
    window.activate_tool("follow_me")
    assert "Path: 2 edge(s)" in window.hint_label.text()
    click(window, (0, 30, 30))
    assert is_closed_manifold(ents(window))
    assert signed_volume(ents(window)) == pytest.approx(2000 * 100 * 100)
    assert not window.viewport.selection.items()
    window.undo()
    assert len(ents(window).faces) == 1


def test_follow_me_click_profile_then_path(scene) -> None:
    window, _ = scene
    window.activate_tool("follow_me")
    click(window, (0, 30, 30))
    assert window.viewport.tool.busy()
    hover(window, (500, 0, 0))
    assert len(window.viewport.hover) == 3, "the profile and the two path edges are pre-highlighted"
    click(window, (500, 0, 0))
    assert signed_volume(ents(window)) == pytest.approx(2000 * 100 * 100)
    assert not window.viewport.tool.busy()


def test_follow_me_around_a_face_perimeter(window) -> None:
    build(window, lambda e: draw.rectangle(e, (0, 0, 0), 1000, 1000))
    build(window, lambda e: draw.face(e, [(0, -100, 0), (0, 0, 0), (0, -100, 100)]))
    window.viewport.set_view("iso")
    window.viewport.zoom_extents()
    window.activate_tool("follow_me")
    click(window, (0, -70, 20))
    click(window, (500, 1000, 0))  # an edge of the square face: its whole perimeter is the path
    faces = [f for f in ents(window).faces.values() if abs(f.normal[2]) < 0.99]
    assert len(faces) >= 4, "a molding runs around all four sides"
    lo, hi = window.document.model.bounds()
    assert np.allclose(lo, (-100, -100, 0), atol=1) and np.allclose(hi, (1100, 1100, 100), atol=1)


def test_intersect_with_model_and_selection(window) -> None:
    model = window.document.model
    group = build(window, lambda e: make_group(model, e, [*primitives.box(e, (0, 0, 0), (1000, 1000, 1000))]))
    build(window, lambda e: primitives.box(e, (500, 500, 500), (1000, 1000, 1000)))
    loose = [*ents(window).faces.values()]
    window.viewport.selection.set(loose)
    edges_before = len(ents(window).edges)
    assert window.intersect("model") > 0
    assert len(ents(window).edges) > edges_before
    assert len(group.definition.entities.edges) > 12, "the group's faces are split too"
    window.undo()
    assert len(ents(window).edges) == edges_before
    window.viewport.selection.set([])
    assert window.intersect("selection") == 0


def test_intersect_with_context_inside_a_group(window) -> None:
    model = window.document.model

    def setup(e):
        primitives.box(e, (0, 0, 0), (1000, 1000, 1000))
        draw.rectangle(e, (500, -200, 500), 1400, 300, draw.plane_axes("yz"))  # crosses the box
        return make_group(model, e, [*e.faces.values(), *e.edges.values()])

    group = build(window, setup)
    window.document.enter(group)
    inner = group.definition.entities
    plane = next(f for f in inner.faces.values() if abs(f.normal[0]) > 0.99 and
                 abs(f.outer_loop[0].position[0] - 500) < 1)
    window.viewport.selection.set([plane])
    assert window.intersect("context") > 0
    on_front = [e for e in inner.edges.values()
                if all(abs(v.position[0] - 500) < 1 and abs(v.position[1]) < 1 for v in e.vertices)]
    assert on_front, "an intersection edge now lies on the box's front face, where the plane passes through it"
    assert sorted(round(v.position[2]) for v in on_front[0].vertices) == [500, 800]
