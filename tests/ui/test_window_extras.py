"""Tool palette, keyboard shortcuts and Model Info."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit

from pymodeler.ops import draw


def test_tool_palette_is_grouped_on_the_left(window) -> None:
    bar = window.tools_toolbar
    assert window.toolBarArea(bar) == Qt.ToolBarArea.LeftToolBarArea
    assert bar.orientation() == Qt.Orientation.Vertical
    assert sum(a.isSeparator() for a in bar.actions()) == 4
    names = [a.text() for a in bar.actions() if not a.isSeparator()]
    assert names[:3] == ["Select", "Eraser", "Paint Bucket"] and names[-1] == "Zoom"
    assert {"Follow Me", "Tape Measure"} <= set(names)


def test_keyboard_shortcuts_list(window) -> None:
    rows = dict(window.shortcut_rows())
    assert rows["Tape Measure"] == "T" and rows["Paint Bucket"] == "B" and rows["Select"] == "Space"
    assert rows["Make Group"] == "Ctrl+G" and rows["Keyboard Shortcuts"] == "F1"
    shown = []
    window.exec_dialog = lambda dialog: shown.append(dialog.text()) or 0
    window.show_shortcuts()
    assert shown and "Tape Measure" in shown[0]


def test_model_info_changes_units_and_name(window) -> None:
    model = window.document.model
    window.document.perform("Setup", lambda: draw.rectangle(model.entities, (0, 0, 0), 1000, 500))

    def fill(dialog: QDialog) -> int:
        dialog.findChild(QLineEdit).setText("Shed")
        dialog.findChild(QComboBox).setCurrentText("m")
        return QDialog.DialogCode.Accepted

    window.exec_dialog = fill
    window.model_info()
    assert window.document.model.units == "m" and window.document.model.name == "Shed"
    edge = next(iter(window.document.model.entities.edges.values()))
    window.viewport.selection.set([edge])
    assert window.panels["entityinfo"].measure_label.text() in ("Length 1m", "Length 0.5m")
    window.undo()
    assert window.document.model.units == "mm" and window.document.model.name == ""
