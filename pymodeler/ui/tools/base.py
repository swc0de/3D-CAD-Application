"""The base class for interactive tools.

A tool receives mouse and key events from the viewport (middle-button navigation is
handled by the viewport itself), draws temporary 2D overlays, and may accept typed
values from the Measurements box (VCB).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter

    from pymodeler.ui.viewport import Viewport


class Tool:
    """Base class: override the event methods you need."""

    name = "Tool"
    shortcut = ""
    hint = ""
    """Status-bar text shown while the tool is active."""
    vcb_label = ""
    """Label of the Measurements box ('' disables it)."""

    def __init__(self, viewport: "Viewport") -> None:
        self.viewport = viewport

    # -- lifecycle ----------------------------------------------------------------------
    def activate(self) -> None:
        """Called when the tool becomes active."""

    def deactivate(self) -> None:
        """Called when another tool takes over."""

    def reset(self) -> None:
        """Abandon any operation in progress (Esc)."""

    # -- events (return True when handled) ----------------------------------------------
    def mouse_press(self, event: "QMouseEvent") -> bool:
        return False

    def mouse_move(self, event: "QMouseEvent") -> bool:
        return False

    def mouse_release(self, event: "QMouseEvent") -> bool:
        return False

    def mouse_double_click(self, event: "QMouseEvent") -> bool:
        return False

    def key_press(self, event: "QKeyEvent") -> bool:
        return False

    def vcb_entered(self, text: str) -> bool:
        """A value was typed in the Measurements box; return True if it was used."""
        return False

    # -- drawing --------------------------------------------------------------------------
    def draw_overlay(self, painter: "QPainter") -> None:
        """Draw temporary 2D graphics on top of the 3D view."""

    def status(self) -> str:
        """Current status-bar hint."""
        return self.hint
