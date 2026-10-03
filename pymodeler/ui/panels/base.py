"""Shared panel plumbing: refresh on model/selection changes, overridable dialogs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QInputDialog, QWidget

if TYPE_CHECKING:
    from pymodeler.ui.document import Document
    from pymodeler.ui.viewport import Viewport


class Panel(QWidget):
    """A side panel bound to the document and viewport."""

    title = "Panel"

    def __init__(self, document: "Document", viewport: "Viewport", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.viewport = viewport
        self._refreshing = False
        document.listeners.append(lambda _doc: self.refresh())
        viewport.selection.listeners.append(lambda _sel: self.selection_changed())

    def refresh(self) -> None:
        """Rebuild the panel from the model."""

    def selection_changed(self) -> None:
        """The viewport selection changed."""

    def perform(self, name: str, action) -> None:  # type: ignore[no-untyped-def]
        """Run an undoable model change, reporting errors in the status bar."""
        try:
            self.document.perform(name, action)
        except (ValueError, KeyError) as exc:
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(f"{name} failed: {exc}", 6000)

    # Dialog hooks (tests replace these) ------------------------------------------------
    def ask_text(self, title: str, label: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

    def ask_color(self, initial: QColor) -> QColor | None:
        color = QColorDialog.getColor(initial, self, "Material colour")
        return color if color.isValid() else None

    def ask_number(self, title: str, label: str, value: float, lo: float, hi: float) -> float | None:
        number, ok = QInputDialog.getDouble(self, title, label, value, lo, hi, 2)
        return number if ok else None
