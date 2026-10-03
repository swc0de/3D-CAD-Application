"""The Tape Measure tool, guides in the viewport, and erasing guides."""

import re

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pymodeler.ops import draw

from .test_draw_tools import at, click, hover, type_vcb


@pytest.fixture
def top(window):
    vp = window.viewport
    vp.set_perspective(False)
    vp.set_view("top")
    vp.camera.ortho_height = 6000.0
    vp.camera.target = np.array([1000.0, 1000.0, 0.0])
    vp.camera.eye = np.array([1000.0, 1000.0, 10000.0])
    model = window.document.model
    window.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 2000, 1000))
    window.activate_tool("tape")
    return window


def guides(window):
    return window.document.model.guides


def test_measuring_from_an_edge_makes_a_parallel_guide(top) -> None:
    click(top, (700, 0, 0))  # on the bottom edge (not its midpoint, which would make a guide point)
    hover(top, (1300, 600, 0))
    shown = re.search(r"Length ([0-9.]+)mm", top.hint_label.text())
    assert shown and float(shown.group(1)) == pytest.approx(600, abs=12), "the perpendicular distance"
    click(top, (1300, 600, 0))
    (guide,) = guides(top)
    assert np.allclose(guide.direction, (1, 0, 0)) or np.allclose(guide.direction, (-1, 0, 0))
    assert guide.distance((0, 600, 0)) == pytest.approx(0, abs=12), "perpendicular offset from the edge"
    assert "Distance" in top.hint_label.text()
    top.undo()
    assert guides(top) == []


def test_typed_offset_and_guide_point(top) -> None:
    click(top, (0, 300, 0))  # on the left edge
    hover(top, (400, 300, 0))
    type_vcb(top, "250")
    (line,) = guides(top)
    assert line.distance((250, 0, 0)) == pytest.approx(0, abs=1e-6), "exactly 250 mm from the edge"
    click(top, (2000, 1000, 0))  # an endpoint: makes a guide point
    hover(top, (2000, 1500, 0))
    type_vcb(top, "300")
    point = guides(top)[-1]
    assert point.is_point and np.allclose(point.point, (2000, 1300, 0), atol=1e-6)


def test_measuring_on_a_face_or_with_ctrl_makes_no_guide(top) -> None:
    click(top, (700, 300, 0))
    click(top, (900, 300, 0))
    assert guides(top) == [] and "Distance" in top.hint_label.text()
    QTest.keyClick(top.viewport, Qt.Key.Key_Control)
    assert not top.viewport.tool.make_guides
    click(top, (700, 0, 0))
    click(top, (700, 700, 0))
    assert guides(top) == []


def test_cursor_snaps_to_guides(top) -> None:
    top.document.perform("Guide", lambda: top.document.model.add_guide((0, 1400, 0), (1, 0, 0)))
    top.activate_tool("line")
    hover(top, (600, 1400, 0))
    assert top.viewport.tool.current.kind == "on_guide"
    assert abs(top.viewport.tool.current.point[1] - 1400) < 1e-6
    top.actions_by_name["guides"].setChecked(False)
    hover(top, (650, 1400, 0))
    assert top.viewport.tool.current.kind != "on_guide", "hidden guides are not snapped to"


def test_resize_model_to_a_typed_length(top) -> None:
    top.document.perform("Guide", lambda: top.document.model.add_guide((0, 1400, 0), (1, 0, 0)))
    QTest.keyClick(top.viewport, Qt.Key.Key_Control)  # measure only
    click(top, (0, 0, 0))
    click(top, (2000, 0, 0))
    asked = []
    top.viewport.tool.confirm_resize = lambda factor: asked.append(factor) or True
    type_vcb(top, "3000")
    assert asked == [pytest.approx(1.5)]
    lo, hi = top.document.model.bounds()
    assert np.allclose(hi - lo, (3000, 1500, 0), atol=1e-6)
    assert guides(top)[0].distance((0, 2100, 0)) == pytest.approx(0, abs=1e-6), "guides scale with the model"
    top.undo()
    lo, hi = top.document.model.bounds()
    assert np.allclose(hi - lo, (2000, 1000, 0), atol=1e-6)


def test_erase_and_delete_guides(top) -> None:
    model = top.document.model
    top.document.perform("Guides", lambda: [model.add_guide((0, 1400, 0), (1, 0, 0)), model.add_guide((0, 1700, 0), (1, 0, 0))])
    top.activate_tool("eraser")
    pos = at(top, (2600, 1400, 0))
    QTest.mouseMove(top.viewport, pos)
    QTest.mouseClick(top.viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert len(guides(top)) == 1 and guides(top)[0].point[1] == pytest.approx(1700)
    assert len(top.document.model.entities.faces) == 1, "the rectangle is untouched"
    top.delete_guides()
    assert guides(top) == []
    top.undo()
    assert len(guides(top)) == 1
