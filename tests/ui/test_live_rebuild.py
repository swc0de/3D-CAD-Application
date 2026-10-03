"""Live rebuild: saving a script in the watched folder rebuilds the model in the app."""

import json
import os

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from pymodeler.ops import draw


def box_script(width: float) -> str:
    return json.dumps({"version": 1, "units": "mm", "steps": [{"op": "box", "id": "b", "size": [width, 500, 300]}]})


def save(path, text: str, stamp: int) -> None:
    path.write_text(text)
    os.utime(path, ns=(stamp * 1_000_000_000, stamp * 1_000_000_000))


def settle(window) -> list:
    """Poll twice: changes are reported once a file has stopped changing."""
    return window.script_watcher.poll() + window.script_watcher.poll()


@pytest.fixture
def live(qapp, tmp_path):
    from pymodeler.ui.main_window import MainWindow

    folder = tmp_path / "scripts"
    folder.mkdir()
    save(folder / "old.json", box_script(100), 1)
    save(folder / "box.json", box_script(1000), 2)
    win = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat), watch_folder=folder)
    win.errors = []
    win.show_error = lambda title, text: win.errors.append((title, text))
    win.maybe_save = lambda: True
    yield win, folder
    win.close()
    win.deleteLater()
    qapp.processEvents()


def width(window) -> float:
    lo, hi = window.document.model.bounds()
    return float(hi[0] - lo[0])


def test_opens_newest_script_and_rebuilds_on_save(live) -> None:
    window, folder = live
    assert window.document.script_path == folder / "box.json", "the newest script is shown on start"
    assert window.actions_by_name["live"].isChecked() and "scripts/" in window.watch_label.text()
    assert width(window) == pytest.approx(1000)
    window.viewport.set_view("front")
    eye = window.viewport.camera.eye.copy()
    save(folder / "box.json", box_script(1500), 3)
    assert settle(window) == [folder / "box.json"]
    assert width(window) == pytest.approx(1500)
    assert np.allclose(window.viewport.camera.eye, eye), "rebuilding the same script keeps the view"
    save(folder / "old.json", box_script(200), 4)
    settle(window)
    assert window.document.script_path == folder / "old.json" and width(window) == pytest.approx(200)


def test_broken_script_shows_a_banner_not_a_dialog(live) -> None:
    window, folder = live
    save(folder / "box.json", '{"version": 1, "steps": [{"op": "box", "size": "huge"}]}', 3)
    settle(window)
    assert window.errors == []
    assert "step 1 (box)" in window.viewport.banner
    assert width(window) == pytest.approx(1000), "the last good model stays on screen"
    save(folder / "box.json", "{ not json", 4)
    settle(window)
    assert "box.json" in window.viewport.banner
    save(folder / "box.json", box_script(800), 5)
    settle(window)
    assert window.viewport.banner == "" and width(window) == pytest.approx(800)


def test_hand_edits_are_protected(live) -> None:
    window, folder = live
    model = window.document.model
    window.document.perform("Draw", lambda: draw.rectangle(model.entities, (0, 0, 2000), 100, 100))
    window.maybe_save = lambda: False  # the user cancels the save prompt
    save(folder / "box.json", box_script(1500), 3)
    settle(window)
    assert width(window) == pytest.approx(1000) and window.document.undo_stack.can_undo
    asked = []
    window.maybe_save = lambda: asked.append(True) or True
    save(folder / "box.json", box_script(1600), 4)
    settle(window)
    assert asked and width(window) == pytest.approx(1600)


def test_toggle_live_rebuild(live) -> None:
    window, folder = live
    window.actions_by_name["live"].setChecked(False)
    assert window.script_watcher is None and window.watch_label.text() == ""
    window.actions_by_name["live"].setChecked(True)
    assert window.script_watcher is not None and window.script_watcher.folder.resolve() == folder.resolve()
    assert window.start_watching(folder / "missing") is False
