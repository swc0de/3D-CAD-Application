"""Drive the drawing tools with simulated mouse clicks and typing."""

import math

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from pymodeler.ops import primitives


@pytest.fixture
def top(window):
    """A window looking straight down in parallel projection."""
    vp = window.viewport
    vp.set_perspective(False)
    window.actions_by_name["perspective"].setChecked(False)
    vp.set_view("top")
    vp.camera.ortho_height = 6000.0
    vp.camera.target = np.array([1000.0, 1000.0, 0.0])
    vp.camera.eye = np.array([1000.0, 1000.0, 10000.0])
    return window


def at(window, point) -> QPoint:
    p = window.viewport.project(np.array(point, dtype=float))
    assert p is not None
    return QPoint(round(p.x()), round(p.y()))


def click(window, point, offset=(0, 0)) -> None:
    vp = window.viewport
    pos = at(window, point) + QPoint(*offset)
    QTest.mouseMove(vp, pos)
    QTest.mouseClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def hover(window, point) -> None:
    QTest.mouseMove(window.viewport, at(window, point))


def type_vcb(window, text: str) -> None:
    window.vcb.setText(text)
    window._vcb_entered()


def entities(window):
    return window.document.model.entities


def test_line_tool_closes_a_face_and_undo_redo(top) -> None:
    top.activate_tool("line")
    for p in [(0, 0, 0), (2000, 0, 0), (2000, 1500, 0), (0, 1500, 0), (0, 0, 0)]:
        click(top, p)
    ents = entities(top)
    assert len(ents.edges) == 4 and len(ents.faces) == 1
    face = next(iter(ents.faces.values()))
    assert face.area() == pytest.approx(2000 * 1500, rel=0.01)  # clicks land on whole pixels
    assert top.actions_by_name["undo"].isEnabled()
    top.undo()
    assert len(entities(top).edges) == 3 and not entities(top).faces
    top.redo()
    assert len(entities(top).faces) == 1


def test_line_typed_length_along_axis(top) -> None:
    top.activate_tool("line")
    click(top, (0, 0, 0))
    hover(top, (1500, 0, 0))
    assert top.viewport.tool.current.kind in ("axis_x", "on_edge")
    type_vcb(top, "2.5m")
    edges = list(entities(top).edges.values())
    assert len(edges) == 1 and edges[0].length() == pytest.approx(2500)
    type_vcb(top, "<0, 1000, 0>")
    assert len(entities(top).edges) == 2


def test_rectangle_tool_click_and_typed(top) -> None:
    top.activate_tool("rectangle")
    click(top, (0, 0, 0))
    click(top, (1000, 800, 0))
    face = next(iter(entities(top).faces.values()))
    assert face.area() == pytest.approx(1000 * 800, rel=0.02)
    click(top, (2500, 0, 0))
    hover(top, (3000, 500, 0))
    type_vcb(top, "1200,600")
    areas = sorted(round(f.area()) for f in entities(top).faces.values())
    assert 720_000 in areas
    assert np.allclose(next(iter(entities(top).faces.values())).normal, (0, 0, 1))


def test_rectangle_on_a_vertical_face(window) -> None:
    window.document.perform("Box", lambda: primitives.box(window.document.model.entities, (0, 0, 0), (2000, 2000, 2000)))
    vp = window.viewport
    vp.set_perspective(False)
    vp.set_view("front")
    window.activate_tool("rectangle")
    click(window, (500, 0, 500))
    hover(window, (1500, 0, 1500))
    type_vcb(window, "800,600")
    front = [f for f in entities(window).faces.values() if f.normal[1] < -0.99]
    assert sorted(round(f.area()) for f in front) == [480_000, 4_000_000 - 480_000]


def test_circle_polygon_and_segments(top) -> None:
    top.activate_tool("circle")
    type_vcb(top, "32s")
    click(top, (0, 0, 0))
    hover(top, (300, 0, 0))
    type_vcb(top, "500")
    face = next(iter(entities(top).faces.values()))
    assert len(face.outer_loop) == 32
    assert face.area() == pytest.approx(0.5 * 32 * 500**2 * math.sin(2 * math.pi / 32))
    top.activate_tool("polygon")
    click(top, (3000, 0, 0))
    hover(top, (3300, 0, 0))
    type_vcb(top, "400")
    hexagon = [f for f in entities(top).faces.values() if len(f.outer_loop) == 6]
    assert len(hexagon) == 1


def test_arc_tool(top) -> None:
    top.activate_tool("arc")
    click(top, (0, 0, 0))
    click(top, (2000, 0, 0))
    hover(top, (1000, 600, 0))
    type_vcb(top, "1000")
    edges = list(entities(top).edges.values())
    assert len(edges) == 12
    ys = [v.position[1] for v in entities(top).vertices.values()]
    assert max(ys) == pytest.approx(1000, abs=1e-6), "a 1000 bulge on a 2000 chord is a semicircle"


def test_escape_cancels_and_typing_goes_to_vcb(top, qapp) -> None:
    top.activate_tool("rectangle")
    click(top, (0, 0, 0))
    QTest.keyClick(top.viewport, Qt.Key.Key_Escape)
    assert not top.viewport.tool.points
    top.viewport.setFocus()
    QTest.keyClicks(top.viewport, "25")
    assert top.vcb.text() == "25"
    top.activate_tool("orbit")
    assert not top.vcb.isEnabled()


def test_arrow_keys_lock_axes(top) -> None:
    top.activate_tool("line")
    click(top, (0, 0, 0))
    QTest.keyClick(top.viewport, Qt.Key.Key_Left)
    hover(top, (1500, 1200, 0))
    inf = top.viewport.tool.current
    assert inf.kind == "locked" and abs(inf.point[0]) < 1e-6, "locked to the green axis"
    QTest.keyClick(top.viewport, Qt.Key.Key_Left)
    assert top.viewport.tool.lock is None
