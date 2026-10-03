"""Drive the selection and editing tools with simulated mouse and keyboard input."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.core.entities import Edge, Face
from pymodeler.ops import draw, primitives

from .test_draw_tools import at, click, hover, type_vcb


def build(window, fn) -> None:
    window.document.perform("Setup", lambda: fn(window.document.model.entities))


def ents(window):
    return window.document.model.entities


@pytest.fixture
def top(window):
    vp = window.viewport
    vp.set_perspective(False)
    vp.set_view("top")
    vp.camera.ortho_height = 6000.0
    vp.camera.target = np.array([1000.0, 1000.0, 0.0])
    vp.camera.eye = np.array([1000.0, 1000.0, 10000.0])
    return window


@pytest.fixture
def iso(window):
    build(window, lambda e: primitives.box(e, (0, 0, 0), (1000, 1000, 1000)))
    window.viewport.set_view("iso")
    window.viewport.zoom_extents()
    return window


def drag(window, a, b, button=Qt.MouseButton.LeftButton) -> None:
    vp = window.viewport
    QTest.mousePress(vp, button, Qt.KeyboardModifier.NoModifier, a)
    for k in range(1, 6):
        QTest.mouseMove(vp, a + (b - a) * (k / 5))
    QTest.mouseRelease(vp, button, Qt.KeyboardModifier.NoModifier, b)


def test_select_click_toggle_and_clear(iso) -> None:
    iso.activate_tool("select")
    click(iso, (500, 500, 1000))
    sel = iso.viewport.selection.items()
    assert len(sel) == 1 and isinstance(sel[0], Face) and sel[0].normal[2] > 0.99
    edge_pt = at(iso, (500, 0, 1000))
    QTest.mouseClick(iso.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, edge_pt)
    assert len(iso.viewport.selection) == 2
    assert any(isinstance(e, Edge) for e in iso.viewport.selection)
    QTest.mouseClick(iso.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 5))
    assert len(iso.viewport.selection) == 0
    assert "Nothing selected" in iso.hint_label.text()


def test_double_and_triple_click(iso) -> None:
    iso.activate_tool("select")
    pos = at(iso, (500, 500, 1000))
    QTest.mouseDClick(iso.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert len(iso.viewport.selection) == 5  # the top face and its four edges
    QTest.mousePress(iso.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseRelease(iso.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert len(iso.viewport.selection) == 18  # 6 faces + 12 edges


def test_window_and_crossing_selection(top) -> None:
    build(top, lambda e: draw.rectangle(e, (0, 0, 0), 1000, 1000))
    build(top, lambda e: draw.rectangle(e, (2000, 0, 0), 1000, 1000))
    top.activate_tool("select")
    drag(top, at(top, (-200, 1200, 0)), at(top, (1200, -200, 0)))
    window_sel = top.viewport.selection.items()
    assert len(window_sel) == 5, "window: one rectangle (face + 4 edges) fully inside"
    drag(top, at(top, (2500, 1200, 0)), at(top, (1500, -200, 0)))
    crossing = top.viewport.selection.items()
    assert len(crossing) >= 4, "crossing: everything the box touches"
    assert all(e.parent is ents(top) for e in crossing)


def test_delete_and_select_all(iso) -> None:
    iso.select_all()
    assert len(iso.viewport.selection) == 18
    iso.delete_selection()
    assert not ents(iso).faces and not ents(iso).edges
    iso.undo()
    assert len(ents(iso).faces) == 6


def test_push_pull_by_typing_and_by_mouse(top) -> None:
    build(top, lambda e: draw.rectangle(e, (0, 0, 0), 1000, 1000))
    top.activate_tool("push_pull")
    click(top, (500, 500, 0))
    type_vcb(top, "1200")
    assert is_closed_manifold(ents(top))
    assert signed_volume(ents(top)) == pytest.approx(1000 * 1000 * 1200)
    top.viewport.set_view("front")
    top.viewport.zoom_extents()
    click(top, (500, 0, 600))
    hover(top, (500, 0, 600))
    QTest.mouseMove(top.viewport, at(top, (500, 0, 600)) + QPoint(0, 0))
    click(top, (500, 0, 600))  # zero distance: nothing happens
    assert signed_volume(ents(top)) == pytest.approx(1000 * 1000 * 1200)


def test_move_copy_and_array(top) -> None:
    build(top, lambda e: draw.rectangle(e, (0, 0, 0), 500, 500))
    top.select_all()
    top.activate_tool("move")
    click(top, (0, 0, 0))
    hover(top, (800, 0, 0))
    type_vcb(top, "1000")
    xs = sorted(v.position[0] for v in ents(top).vertices.values())
    assert xs[0] == pytest.approx(1000) and xs[-1] == pytest.approx(1500)
    QTest.keyClick(top.viewport, Qt.Key.Key_Control)
    click(top, (1000, 0, 0))
    hover(top, (1000, 800, 0))
    type_vcb(top, "1000")
    assert len(ents(top).faces) == 2
    type_vcb(top, "3x")
    assert len(ents(top).faces) == 4, "the copy becomes three copies in a row"
    ys = sorted({round(v.position[1]) for v in ents(top).vertices.values()})
    assert ys[-1] == 3500


def test_rotate_and_scale(top) -> None:
    build(top, lambda e: draw.rectangle(e, (0, 0, 0), 1000, 500))
    top.select_all()
    top.activate_tool("rotate")
    click(top, (0, 0, 0))
    click(top, (1000, 0, 0))
    hover(top, (0, 1000, 0))
    assert top.viewport.tool.angle == pytest.approx(90)
    type_vcb(top, "90")
    lo, hi = ents(top).bounds()
    assert np.allclose(lo, (-500, 0, 0), atol=1e-6) and np.allclose(hi, (0, 1000, 0), atol=1e-6)
    top.activate_tool("scale")
    click(top, (0, 0, 0))
    click(top, (0, 1000, 0))
    type_vcb(top, "2")
    lo, hi = ents(top).bounds()
    assert np.allclose(hi - lo, (1000, 2000, 0), atol=1e-6)


def test_offset_and_eraser(top) -> None:
    build(top, lambda e: draw.rectangle(e, (0, 0, 0), 1000, 1000))
    top.activate_tool("offset")
    click(top, (500, 500, 0))
    type_vcb(top, "100")
    assert sorted(round(f.area()) for f in ents(top).faces.values()) == [360_000, 640_000]
    top.activate_tool("eraser")
    click(top, (500, 0, 0))
    assert len(ents(top).faces) == 1, "erasing an outer edge removes the ring face"
    top.undo()
    assert len(ents(top).faces) == 2
