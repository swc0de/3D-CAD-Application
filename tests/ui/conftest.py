"""Qt fixtures for GUI tests (no pytest-qt needed).

Without a display the tests use Qt's offscreen platform, which cannot create OpenGL
widgets, so GL-rendering tests are skipped there. Run them under a virtual X server:
``xvfb-run -a python -m pytest tests/ui``.
"""

import os

import pytest

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 / system Qt libraries unavailable")


@pytest.fixture(scope="session")
def qapp():
    from pymodeler.ui.app import configure_opengl

    app = QtWidgets.QApplication.instance()
    if app is None:
        configure_opengl()
        app = QtWidgets.QApplication(["pytest"])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from pymodeler.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings=settings)
    win.errors = []
    win.show_error = lambda title, text: win.errors.append((title, text))
    win.maybe_save = lambda: True
    win.resize(800, 600)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def gl_available(qapp) -> bool:
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.platformName() not in ("offscreen", "minimal")
