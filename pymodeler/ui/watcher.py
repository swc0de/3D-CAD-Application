"""Live rebuild: poll a folder of build scripts from the Qt event loop."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer

from pymodeler.script.watch import FolderWatcher

POLL_MS = 400


class ScriptWatcher(QObject):
    """Calls ``on_change(path)`` whenever a build script in ``folder`` is saved."""

    def __init__(self, folder: str | Path, on_change: Callable[[Path], None], interval_ms: int = POLL_MS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.watcher = FolderWatcher(folder)
        self.on_change = on_change
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.poll)

    @property
    def folder(self) -> Path:
        return self.watcher.folder

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def poll(self) -> list[Path]:
        """Check the folder now; returns the scripts that were rebuilt."""
        changed = self.watcher.poll()
        for path in changed:
            self.on_change(path)
        return changed
