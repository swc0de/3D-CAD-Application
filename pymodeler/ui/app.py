"""Start the desktop application."""

from __future__ import annotations

import sys
from pathlib import Path


def configure_opengl() -> None:
    """Request an OpenGL 3.3 core profile with depth and multisampling (call before QApplication)."""
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


def run_app(path: str | Path | None = None) -> int:
    """Run PyModeler's GUI; returns the exit code."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(
            "error: the PyModeler app needs PySide6 and system OpenGL libraries "
            f"({exc}).\nInstall them with: pip install PySide6  (Linux: apt install libegl1 libgl1 libxkbcommon0)\n"
            "Build scripts work without the GUI: python -m pymodeler build script.json --preview out.png",
            file=sys.stderr,
        )
        return 1
    from pymodeler.ui.main_window import MainWindow

    configure_opengl()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PyModeler")
    app.setOrganizationName("PyModeler")
    window = MainWindow(path)
    window.show()
    return int(app.exec())
