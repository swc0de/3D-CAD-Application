"""The main application window: menus, toolbars, viewport and status bar."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStyle,
    QToolBar,
)

from pymodeler import __version__
from pymodeler.ui.document import OPENABLE, Document, push_recent
from pymodeler.ui.tools.base import Tool
from pymodeler.ui.tools.draw_tools import ArcTool, CircleTool, LineTool, PolygonTool, RectangleTool
from pymodeler.ui.tools.navigate import OrbitTool, PanTool, ZoomTool
from pymodeler.ui.viewport import Viewport

MODEL_FILTER = "PyModeler models (*.pym);;Build scripts (*.json);;Wavefront OBJ (*.obj);;All files (*)"
SCRIPT_FILTER = "Build scripts (*.json)"
EXPORT_FILTER = "glTF binary (*.glb);;glTF (*.gltf);;Wavefront OBJ (*.obj);;STL (*.stl);;PyModeler (*.pym)"

VIEW_SHORTCUTS = {"iso": "F8", "top": "F2", "front": "F3", "right": "F4", "back": "F5", "left": "F6", "bottom": "F7"}


class MainWindow(QMainWindow):
    """PyModeler's main window."""

    def __init__(self, path: str | Path | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("PyModeler", "PyModeler")
        self.document = Document()
        self.viewport = Viewport(self.document, self)
        self.setCentralWidget(self.viewport)
        self.tools: dict[str, Tool] = {}
        self.tool_actions: dict[str, QAction] = {}
        self.actions_by_name: dict[str, QAction] = {}
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()
        self._register_tools()
        self.document.listeners.append(self._on_document_changed)
        self.viewport.on_tool_status = self.show_hint
        self.activate_tool("orbit")
        self._update_title()
        self.resize(1280, 820)
        if path is not None:
            self.open_path(path)

    # ------------------------------------------------------------------ setup
    def _action(self, name: str, text: str, slot: Callable[[], object], shortcut: str | QKeySequence | None = None,
                tip: str = "", checkable: bool = False, icon: QStyle.StandardPixmap | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(f"{text.replace('&', '')} ({action.shortcut().toString()})" if shortcut else tip)
        if icon is not None:
            action.setIcon(self.style().standardIcon(icon))
        action.setCheckable(checkable)
        if checkable:
            action.toggled.connect(lambda on, f=slot: f(on))  # type: ignore[misc]
        else:
            action.triggered.connect(lambda _=False, f=slot: f())
        self.actions_by_name[name] = action
        self.addAction(action)
        return action

    def _build_actions(self) -> None:
        sp = QStyle.StandardPixmap
        self._action("new", "&New", self.new_document, QKeySequence.StandardKey.New, "Start an empty model", icon=sp.SP_FileIcon)
        self._action("open", "&Open...", self.open_dialog, QKeySequence.StandardKey.Open,
                     "Open a .pym model, an .obj file or a .json build script", icon=sp.SP_DialogOpenButton)
        self._action("save", "&Save", self.save, QKeySequence.StandardKey.Save, "Save the model", icon=sp.SP_DialogSaveButton)
        self._action("save_as", "Save &As...", self.save_as, QKeySequence.StandardKey.SaveAs, "Save under a new name")
        self._action("run_script", "&Run Build Script...", self.run_script_dialog, "Ctrl+R",
                     "Build a model from a JSON build script", icon=sp.SP_MediaPlay)
        self._action("rebuild", "Re&build Script", self.rebuild, "F9", "Run the last build script again",
                     icon=sp.SP_BrowserReload)
        self._action("export", "&Export...", self.export_dialog, "Ctrl+E", "Export to glTF/GLB, OBJ or STL")
        self._action("quit", "&Quit", self.close, QKeySequence.StandardKey.Quit, "Quit PyModeler")
        undo = self._action("undo", "&Undo", self.undo, QKeySequence.StandardKey.Undo, "Undo the last command",
                            icon=sp.SP_ArrowBack)
        redo = self._action("redo", "&Redo", self.redo, "Ctrl+Y", "Redo the last undone command",
                            icon=sp.SP_ArrowForward)
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        undo.setEnabled(False)
        redo.setEnabled(False)
        self._action("zoom_extents", "Zoom E&xtents", self.viewport.zoom_extents, "Shift+Z",
                     "Fit the whole model in view")
        for name, key in VIEW_SHORTCUTS.items():
            self._action(f"view_{name}", name.capitalize(), lambda n=name: self.set_view(n), key,
                         f"{name.capitalize()} view")
        persp = self._action("perspective", "&Perspective", self.set_perspective, "F10",
                             "Toggle perspective / parallel projection", checkable=True)
        persp.setChecked(True)
        self._action("grid", "Ground &Grid", self.set_grid, "", "Show a grid on the ground", checkable=True)
        axes = self._action("axes", "&Axes", self.set_axes, "", "Show the red/green/blue axes", checkable=True)
        axes.setChecked(True)
        self._action("about", "&About PyModeler", self.about, "", "About this application")

    def _build_menus(self) -> None:
        a = self.actions_by_name
        file_menu = self.menuBar().addMenu("&File")
        for name in ("new", "open"):
            file_menu.addAction(a[name])
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self._refresh_recent_menu()
        file_menu.addSeparator()
        for name in ("save", "save_as"):
            file_menu.addAction(a[name])
        file_menu.addSeparator()
        for name in ("run_script", "rebuild", "export"):
            file_menu.addAction(a[name])
        file_menu.addSeparator()
        file_menu.addAction(a["quit"])

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(a["undo"])
        edit_menu.addAction(a["redo"])
        view_menu = self.menuBar().addMenu("&Camera")
        view_menu.addAction(a["zoom_extents"])
        views = view_menu.addMenu("&Standard Views")
        for name in VIEW_SHORTCUTS:
            views.addAction(a[f"view_{name}"])
        view_menu.addAction(a["perspective"])
        display = self.menuBar().addMenu("&View")
        display.addAction(a["axes"])
        display.addAction(a["grid"])
        self.tools_menu = self.menuBar().addMenu("&Tools")
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(a["about"])

    def _build_toolbars(self) -> None:
        a = self.actions_by_name
        files = QToolBar("File", self)
        files.setObjectName("file_toolbar")
        for name in ("new", "open", "save", "run_script", "rebuild", "undo", "redo"):
            files.addAction(a[name])
        self.addToolBar(files)
        self.tools_toolbar = QToolBar("Tools", self)
        self.tools_toolbar.setObjectName("tools_toolbar")
        self.tools_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(self.tools_toolbar)
        views = QToolBar("Views", self)
        views.setObjectName("views_toolbar")
        views.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        views.addAction(a["zoom_extents"])
        for name in ("iso", "top", "front", "right", "back", "left"):
            views.addAction(a[f"view_{name}"])
        views.addAction(a["perspective"])
        self.addToolBar(views)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.hint_label = QLabel("")
        bar.addWidget(self.hint_label, 1)
        self.vcb_label = QLabel("Measurements")
        self.vcb = QLineEdit()
        self.vcb.setFixedWidth(160)
        self.vcb.setPlaceholderText("")
        self.vcb.returnPressed.connect(self._vcb_entered)
        bar.addPermanentWidget(self.vcb_label)
        bar.addPermanentWidget(self.vcb)

    def _register_tools(self) -> None:
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        for key, cls in (
            ("line", LineTool), ("rectangle", RectangleTool), ("circle", CircleTool), ("arc", ArcTool),
            ("polygon", PolygonTool), ("orbit", OrbitTool), ("pan", PanTool), ("zoom", ZoomTool),
        ):
            self.add_tool(key, cls(self.viewport))
        self.viewport.on_type = self._type_into_vcb

    def add_tool(self, key: str, tool: Tool) -> QAction:
        """Register a tool with a menu entry, toolbar button and shortcut."""
        self.tools[key] = tool
        action = QAction(tool.name, self)
        action.setCheckable(True)
        if tool.shortcut:
            action.setShortcut(QKeySequence(tool.shortcut))
            action.setToolTip(f"{tool.name} ({tool.shortcut})")
        action.setStatusTip(tool.hint)
        action.triggered.connect(lambda _=False, k=key: self.activate_tool(k))
        self.tool_group.addAction(action)
        self.tools_menu.addAction(action)
        self.tools_toolbar.addAction(action)
        self.addAction(action)
        self.tool_actions[key] = action
        return action

    # ------------------------------------------------------------------ tools & hints
    def activate_tool(self, key: str) -> None:
        """Switch the active tool."""
        tool = self.tools[key]
        self.tool_actions[key].setChecked(True)
        self.viewport.set_tool(tool)
        self.vcb_label.setText(tool.vcb_label or "Measurements")
        self.vcb.setEnabled(bool(tool.vcb_label))
        self.vcb.clear()
        self.viewport.setFocus()

    def show_hint(self, text: str) -> None:
        self.hint_label.setText(text)

    def _type_into_vcb(self, text: str) -> bool:
        """SketchUp-style: start typing anywhere and it goes to the Measurements box."""
        if not self.vcb.isEnabled():
            return False
        self.vcb.setFocus()
        self.vcb.insert(text)
        return True

    def _vcb_entered(self) -> None:
        tool = self.viewport.tool
        if tool is not None and tool.vcb_entered(self.vcb.text()):
            self.vcb.clear()
            self.viewport.update()
        self.viewport.setFocus()

    # ------------------------------------------------------------------ document actions
    def _on_document_changed(self, _doc: Document) -> None:
        self._update_title()
        stack = self.document.undo_stack
        undo, redo = self.actions_by_name["undo"], self.actions_by_name["redo"]
        undo.setEnabled(stack.can_undo)
        redo.setEnabled(stack.can_redo)
        undo.setText(f"&Undo {stack.undo_name}".rstrip())
        redo.setText(f"&Redo {stack.redo_name}".rstrip())
        self.viewport.refresh()

    def undo(self) -> None:
        if self.document.undo() and self.viewport.tool is not None:
            self.viewport.tool.reset()

    def redo(self) -> None:
        if self.document.redo() and self.viewport.tool is not None:
            self.viewport.tool.reset()

    def _update_title(self) -> None:
        self.setWindowTitle(self.document.title)

    def new_document(self) -> None:
        if not self.maybe_save():
            return
        self.document.new()
        self.viewport.zoom_extents()

    def open_dialog(self) -> None:
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open", self._last_dir(), MODEL_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        """Open a model or build script; shows an error and returns False on failure."""
        path = Path(path)
        if path.suffix.lower() not in OPENABLE:
            self.show_error("Cannot open file", f"{path.name}: unsupported file type (use .pym, .obj or .json)")
            return False
        try:
            self.document.open(path)
        except Exception as exc:  # noqa: BLE001 - report every failure in a dialog
            self.show_error(f"Could not open {path.name}", self._describe(exc))
            return False
        self._remember(path)
        self.viewport.zoom_extents()
        self.statusBar().showMessage(f"Opened {path.name}", 5000)
        return True

    def run_script_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Run Build Script", self._last_dir(), SCRIPT_FILTER)
        if path:
            self.run_script(path)

    def run_script(self, path: str | Path, keep_view: bool = False) -> bool:
        """Build a script into the document (errors are shown, not raised)."""
        path = Path(path)
        try:
            result = self.document.run_script(path)
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"Build failed: {path.name}", self._describe(exc))
            return False
        self._remember(path)
        if not keep_view:
            self.viewport.zoom_extents()
        stats = result.model.stats()
        self.statusBar().showMessage(
            f"Built {path.name}: {stats['faces']} faces, {stats['instances']} groups/components", 8000)
        return True

    def rebuild(self) -> None:
        """Re-run the current build script, keeping the camera."""
        if self.document.script_path is None:
            self.statusBar().showMessage("No build script to rebuild; use File > Run Build Script", 5000)
            return
        self.run_script(self.document.script_path, keep_view=True)

    def save(self) -> bool:
        if self.document.path is None:
            return self.save_as()
        return self._save_to(self.document.path)

    def save_as(self) -> bool:
        suggested = self.document.path or (self.document.script_path.with_suffix(".pym") if self.document.script_path else Path(self._last_dir()) / "model.pym")
        path, _ = QFileDialog.getSaveFileName(self, "Save As", str(suggested), "PyModeler models (*.pym)")
        return bool(path) and self._save_to(Path(path))

    def _save_to(self, path: Path) -> bool:
        try:
            saved = self.document.save(path)
        except Exception as exc:  # noqa: BLE001
            self.show_error("Save failed", self._describe(exc))
            return False
        self._remember(saved)
        self.statusBar().showMessage(f"Saved {saved}", 5000)
        return True

    def export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export", self._last_dir(), EXPORT_FILTER)
        if not path:
            return
        try:
            self.document.export(path)
        except Exception as exc:  # noqa: BLE001
            self.show_error("Export failed", self._describe(exc))
            return
        self.statusBar().showMessage(f"Exported {path}", 5000)

    def maybe_save(self) -> bool:
        """Ask to save unsaved changes; returns False if the user cancels."""
        if not self.document.modified:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save changes to the current model?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------ view actions
    def set_view(self, name: str) -> None:
        self.viewport.set_view(name)

    def set_perspective(self, on: bool) -> None:
        self.viewport.set_perspective(on)

    def set_grid(self, on: bool) -> None:
        self.viewport.show_grid = on
        self.viewport.refresh()

    def set_axes(self, on: bool) -> None:
        self.viewport.show_axes = on
        self.viewport.refresh()

    def about(self) -> None:
        QMessageBox.about(
            self, "About PyModeler",
            f"<b>PyModeler {__version__}</b><p>A SketchUp-style 3D modeler in Python, "
            "scriptable with JSON build scripts.</p>",
        )

    # ------------------------------------------------------------------ helpers
    def show_error(self, title: str, text: str) -> None:
        """Show an error dialog (tests replace this)."""
        QMessageBox.critical(self, title, text)

    @staticmethod
    def _describe(exc: BaseException) -> str:
        from pymodeler.script.errors import ScriptError

        if isinstance(exc, (ScriptError, ValueError, OSError)):
            return str(exc)
        return f"{exc!r}\n\n{traceback.format_exc(limit=4)}"

    def _remember(self, path: Path) -> None:
        recent = push_recent(self.recent_files(), path)
        self.settings.setValue("recent_files", recent)
        self.settings.setValue("last_dir", str(Path(path).resolve().parent))
        self._refresh_recent_menu()

    def recent_files(self) -> list[str]:
        value = self.settings.value("recent_files", [])
        if isinstance(value, str):
            return [value]
        return [str(v) for v in (value or [])]

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", str(Path.cwd())))

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        files = self.recent_files()
        for path in files:
            action = self.recent_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(lambda _=False, p=path: self._open_recent(p))
        self.recent_menu.setEnabled(bool(files))

    def _open_recent(self, path: str) -> None:
        if self.maybe_save():
            self.open_path(path)

