"""Tests for the main window, actions and viewport interaction."""

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from .conftest import gl_available

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def shortcut(window, name: str) -> str:
    return window.actions_by_name[name].shortcut().toString()


def test_actions_and_shortcuts(window) -> None:
    assert shortcut(window, "run_script") == "Ctrl+R"
    assert shortcut(window, "zoom_extents") == "Shift+Z"
    assert shortcut(window, "view_top") == "F2"
    assert window.actions_by_name["open"].shortcut() == QKeySequence(QKeySequence.StandardKey.Open)
    assert {k: window.tool_actions[k].shortcut().toString() for k in ("orbit", "pan", "zoom")} == {
        "orbit": "O", "pan": "H", "zoom": "Z"}
    assert window.windowTitle() == "Untitled - PyModeler"


def test_open_example_frames_model(window) -> None:
    assert window.open_path(EXAMPLES / "04_house.json")
    assert window.windowTitle().startswith("04_house.json *")
    lo, hi = window.document.model.bounds()
    assert np.allclose(window.viewport.camera.target, (lo + hi) / 2)
    assert window.recent_files() and window.recent_files()[0].endswith("04_house.json")
    assert window.recent_menu.isEnabled()


def test_bad_script_shows_error_and_keeps_model(window, tmp_path) -> None:
    window.open_path(EXAMPLES / "01_box.json")
    model = window.document.model
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "steps": [{"op": "circle"}]}')
    assert not window.run_script(bad)
    assert window.document.model is model
    assert "missing required parameter 'radius'" in window.errors[-1][1]
    assert not window.open_path(tmp_path / "model.skp")


def test_rebuild_keeps_camera(window, tmp_path) -> None:
    script = tmp_path / "s.json"
    script.write_text('{"version": 1, "steps": [{"op": "box", "size": [1000, 1000, 1000]}]}')
    window.run_script(script)
    window.set_view("front")
    eye = window.viewport.camera.eye.copy()
    script.write_text('{"version": 1, "steps": [{"op": "box", "size": [1000, 1000, 2000]}]}')
    window.rebuild()
    assert np.allclose(window.viewport.camera.eye, eye)
    assert window.document.model.bounds()[1][2] == pytest.approx(2000)


def test_save_clears_modified(window, tmp_path) -> None:
    window.open_path(EXAMPLES / "01_box.json")
    assert window.document.modified
    assert window._save_to(tmp_path / "box.pym")
    assert not window.document.modified and window.windowTitle().startswith("box.pym - ")
    window.document.new()
    assert window.open_path(tmp_path / "box.pym")
    assert window.document.model.stats()["faces"] == 6


def test_tools_views_and_projection(window) -> None:
    window.tool_actions["pan"].trigger()
    assert window.viewport.tool is window.tools["pan"]
    assert "pan" in window.hint_label.text().lower()
    window.actions_by_name["view_top"].trigger()
    assert window.viewport.view_name == "top"
    assert np.allclose(window.viewport.camera.basis()[2], (0, 0, 1))
    window.actions_by_name["perspective"].setChecked(False)
    assert not window.viewport.camera.perspective
    window.actions_by_name["grid"].setChecked(True)
    assert window.viewport.scene().grid_spacing > 0


def test_mouse_navigation(window, qapp) -> None:
    window.open_path(EXAMPLES / "01_box.json")
    vp = window.viewport
    eye = vp.camera.eye.copy()
    center = QPoint(vp.width() // 2, vp.height() // 2)
    QTest.mousePress(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(vp, center + QPoint(60, 0))
    QTest.mouseRelease(vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, center + QPoint(60, 0))
    assert not np.allclose(vp.camera.eye, eye), "middle-drag orbits"
    distance = vp.camera.distance()
    window.activate_tool("zoom")
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(vp, center + QPoint(0, -50))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center + QPoint(0, -50))
    assert vp.camera.distance() < distance, "dragging up with Zoom zooms in"


RENDER_CHECK = """
import sys
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from pymodeler.ui.app import configure_opengl
configure_opengl()
app = QApplication(["check"])
from pymodeler.ui.main_window import MainWindow
win = MainWindow(settings=QSettings(sys.argv[2], QSettings.Format.IniFormat))
win.maybe_save = lambda: True
win.resize(800, 600)
win.show()
assert win.open_path(sys.argv[1])
app.processEvents()
image = win.viewport.grabFramebuffer()
c = image.pixelColor(image.width() // 2, image.height() // 2)
print(win.viewport.gl_error, image.width(), c.red(), c.green(), c.blue())
"""


def test_viewport_renders_model(qapp, tmp_path) -> None:
    """Render through the real Qt OpenGL widget (in a fresh process: Mesa cannot mix the
    headless EGL contexts other tests create with Qt's GLX context in one process)."""
    import subprocess
    import sys

    if not gl_available(qapp):
        pytest.skip("needs a display with OpenGL (run under xvfb-run)")
    out = subprocess.run(
        [sys.executable, "-c", RENDER_CHECK, str(EXAMPLES / "01_box.json"), str(tmp_path / "s.ini")],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    error, width, r, g, b = out.stdout.split()[-5:]
    assert error == "None" and int(width) > 0
    assert int(r) > int(b), "the cardboard-coloured box fills the middle of the view"
