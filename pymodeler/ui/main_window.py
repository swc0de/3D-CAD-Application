"""The main application window: menus, toolbars, viewport and status bar."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStyle,
    QToolBar,
)

from pymodeler import __version__
from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Entity
from pymodeler.core.units import MM_PER_UNIT, normalize_unit
from pymodeler.ops.edit import WorldFace, intersect_faces, world_faces
from pymodeler.ops.organize import explode, make_group, make_unique, set_hidden
from pymodeler.ui.document import OPENABLE, Document, push_recent
from pymodeler.ui.panels.base import Panel
from pymodeler.ui.panels.entity_info import EntityInfoPanel
from pymodeler.ui.panels.materials import MaterialsPanel
from pymodeler.ui.panels.outliner import OutlinerPanel
from pymodeler.ui.panels.tags import TagsPanel
from pymodeler.ui.tools.base import Tool
from pymodeler.ui.tools.draw_tools import ArcTool, CircleTool, LineTool, PolygonTool, RectangleTool
from pymodeler.ui.tools.edit_tools import (
    EraserTool,
    MoveTool,
    OffsetTool,
    PushPullTool,
    RotateTool,
    ScaleTool,
    SelectTool,
)
from pymodeler.ui.tools.follow_me_tool import FollowMeTool
from pymodeler.ui.tools.navigate import OrbitTool, PanTool, ZoomTool
from pymodeler.ui.tools.paint_tool import PaintTool
from pymodeler.ui.tools.tape_tool import TapeTool
from pymodeler.ui.viewport import Viewport
from pymodeler.ui.watcher import ScriptWatcher

MODEL_FILTER = "PyModeler models (*.pym);;Build scripts (*.json);;Wavefront OBJ (*.obj);;All files (*)"
SCRIPT_FILTER = "Build scripts (*.json)"
EXPORT_FILTER = "glTF binary (*.glb);;glTF (*.gltf);;Wavefront OBJ (*.obj);;STL (*.stl);;PyModeler (*.pym)"

TOOL_GROUP_STARTS = ("line", "push_pull", "tape", "orbit")
"""Tools that begin a new group in the tool palette (drawing, editing, construction, camera)."""
ENTITY_ACTIONS = ("make_group", "make_component", "edit_group", "close_group", "explode", "make_unique")
VIEW_SHORTCUTS = {"iso": "F8", "top": "F2", "front": "F3", "right": "F4", "back": "F5", "left": "F6", "bottom": "F7"}


class MainWindow(QMainWindow):
    """PyModeler's main window."""

    def __init__(self, path: str | Path | None = None, settings: QSettings | None = None,
                 watch_folder: str | Path | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("PyModeler", "PyModeler")
        self.document = Document()
        self.viewport = Viewport(self.document, self)
        self.setCentralWidget(self.viewport)
        self.tools: dict[str, Tool] = {}
        self.tool_actions: dict[str, QAction] = {}
        self.actions_by_name: dict[str, QAction] = {}
        self.panels: dict[str, Panel] = {}
        self.docks: dict[str, QDockWidget] = {}
        self.script_watcher: ScriptWatcher | None = None
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()
        self._register_tools()
        self._build_panels()
        self.document.listeners.append(self._on_document_changed)
        self.viewport.on_context_menu = self.show_context_menu
        self.viewport.on_tool_status = self.show_hint
        self.activate_tool("select")
        self._update_title()
        self.resize(1280, 820)
        if path is not None:
            self.open_path(path)
        if watch_folder is not None:
            self.start_watching(watch_folder)

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
        self._action("live", "&Live Rebuild", self.set_live_rebuild, "",
                     "Rebuild build scripts automatically whenever one in the watch folder is saved",
                     checkable=True)
        self._action("watch_folder", "&Watch Folder...", self.choose_watch_folder, "",
                     "Choose the folder of build scripts to rebuild live")
        self._action("quit", "&Quit", self.close, QKeySequence.StandardKey.Quit, "Quit PyModeler")
        undo = self._action("undo", "&Undo", self.undo, QKeySequence.StandardKey.Undo, "Undo the last command",
                            icon=sp.SP_ArrowBack)
        redo = self._action("redo", "&Redo", self.redo, "Ctrl+Y", "Redo the last undone command",
                            icon=sp.SP_ArrowForward)
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        undo.setEnabled(False)
        redo.setEnabled(False)
        self._action("delete", "&Delete", self.delete_selection, QKeySequence.StandardKey.Delete,
                     "Erase the selected entities")
        self._action("select_all", "Select &All", self.select_all, QKeySequence.StandardKey.SelectAll,
                     "Select everything in the current context")
        self._action("select_none", "Select &None", self.select_none, "Ctrl+T", "Clear the selection")
        self._action("hide", "&Hide", self.hide_selection, "", "Hide the selected entities")
        self._action("unhide_all", "Unhide &All", self.unhide_all, "", "Show every hidden entity in the current context")
        self._action("make_group", "Make &Group", self.make_group, "Ctrl+G", "Group the selected entities")
        self._action("make_component", "Make &Component...", self.make_component, "G",
                     "Turn the selection into a reusable component")
        self._action("edit_group", "&Edit Group/Component", self.edit_group, "",
                     "Open the selected group or component to edit its contents")
        self._action("close_group", "C&lose Group/Component", self.close_group, "",
                     "Stop editing the open group or component (Esc with the Select tool)")
        self._action("explode", "E&xplode", self.explode_selection, "",
                     "Replace the selected groups/components by their contents")
        self._action("make_unique", "Make &Unique", self.make_unique_selection, "",
                     "Give the selected component copies their own definition")
        self._action("intersect_model", "With &Model", lambda: self.intersect("model"), "",
                     "Add edges where the selected faces and groups cross anything in the model")
        self._action("intersect_selection", "With &Selection", lambda: self.intersect("selection"), "",
                     "Add edges where the selected faces and groups cross each other")
        self._action("intersect_context", "With &Context", lambda: self.intersect("context"), "",
                     "Add edges where the selection crosses the rest of the group being edited")
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
        guides = self._action("guides", "G&uides", self.set_guides, "", "Show construction guides", checkable=True)
        guides.setChecked(True)
        self._action("delete_guides", "Delete &Guides", self.delete_guides, "", "Erase every construction guide")
        self._action("about", "&About PyModeler", self.about, "", "About this application")
        self._action("shortcuts", "&Keyboard Shortcuts", self.show_shortcuts, "F1", "List every tool and shortcut")
        self._action("model_info", "Model &Info...", self.model_info, "", "Model name and display units")

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
        for name in ("run_script", "rebuild", "live", "watch_folder", "export"):
            file_menu.addAction(a[name])
        file_menu.addSeparator()
        file_menu.addAction(a["model_info"])
        file_menu.addSeparator()
        file_menu.addAction(a["quit"])

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(a["undo"])
        edit_menu.addAction(a["redo"])
        edit_menu.addSeparator()
        for name in ("delete", "select_all", "select_none"):
            edit_menu.addAction(a[name])
        edit_menu.addSeparator()
        for name in ("hide", "unhide_all", "delete_guides"):
            edit_menu.addAction(a[name])
        edit_menu.addSeparator()
        for name in ENTITY_ACTIONS:
            edit_menu.addAction(a[name])
        self.intersect_menu = edit_menu.addMenu("&Intersect Faces")
        for name in ("intersect_model", "intersect_selection", "intersect_context"):
            self.intersect_menu.addAction(a[name])
        view_menu = self.menuBar().addMenu("&Camera")
        view_menu.addAction(a["zoom_extents"])
        views = view_menu.addMenu("&Standard Views")
        for name in VIEW_SHORTCUTS:
            views.addAction(a[f"view_{name}"])
        view_menu.addAction(a["perspective"])
        display = self.menuBar().addMenu("&View")
        display.addAction(a["axes"])
        display.addAction(a["grid"])
        display.addAction(a["guides"])
        display.addSeparator()
        self.panels_menu = display.addMenu("&Panels")
        self.tools_menu = self.menuBar().addMenu("&Tools")
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(a["shortcuts"])
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
        self.tools_toolbar.setOrientation(Qt.Orientation.Vertical)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.tools_toolbar)
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
        self.watch_label = QLabel("")
        bar.addPermanentWidget(self.watch_label)
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
            ("select", SelectTool), ("eraser", EraserTool), ("paint", PaintTool),
            ("line", LineTool), ("rectangle", RectangleTool), ("circle", CircleTool), ("arc", ArcTool),
            ("polygon", PolygonTool),
            ("push_pull", PushPullTool), ("move", MoveTool), ("rotate", RotateTool), ("scale", ScaleTool),
            ("offset", OffsetTool), ("follow_me", FollowMeTool), ("tape", TapeTool),
            ("orbit", OrbitTool), ("pan", PanTool), ("zoom", ZoomTool),
        ):
            self.add_tool(key, cls(self.viewport))
        self.viewport.selection.listeners.append(lambda _s: self._selection_changed())
        self.viewport.on_type = self._type_into_vcb

    def _build_panels(self) -> None:
        """Materials, Tags, Outliner and Entity Info as tabbed docks on the right."""
        panels: list[Panel] = [
            EntityInfoPanel(self.document, self.viewport),
            MaterialsPanel(self.document, self.viewport, on_pick=lambda: self.activate_tool("paint")),
            TagsPanel(self.document, self.viewport),
            OutlinerPanel(self.document, self.viewport),
        ]
        first: QDockWidget | None = None
        for panel in panels:
            key = type(panel).__name__.removesuffix("Panel").lower()
            dock = QDockWidget(panel.title, self)
            dock.setObjectName(f"{key}_dock")
            dock.setWidget(panel)
            dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            if first is None:
                first = dock
            elif key in ("materials", "tags"):
                self.tabifyDockWidget(first, dock)
            self.panels[key] = panel
            self.docks[key] = dock
            self.panels_menu.addAction(dock.toggleViewAction())
        if first is not None:
            first.raise_()
        self.resizeDocks([first] if first else [], [280], Qt.Orientation.Horizontal)

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
        if key in TOOL_GROUP_STARTS and self.tool_actions:
            self.tools_toolbar.addSeparator()
            self.tools_menu.addSeparator()
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
        self._update_entity_actions()

    def _selection_changed(self) -> None:
        if self.viewport.tool is not None:
            self.show_hint(self.viewport.tool.status())
        self._update_entity_actions()

    def _update_entity_actions(self) -> None:
        """Enable the group/component commands that apply to the selection."""
        a = self.actions_by_name
        items = self.selected_in_context()
        instances = [e for e in items if isinstance(e, ComponentInstance)]
        a["make_group"].setEnabled(bool(items))
        a["make_component"].setEnabled(bool(items))
        a["hide"].setEnabled(bool(items))
        a["edit_group"].setEnabled(len(instances) == 1 and len(items) == 1)
        a["explode"].setEnabled(bool(instances))
        a["make_unique"].setEnabled(any(len(i.definition.instances) > 1 for i in instances))
        a["close_group"].setEnabled(bool(self.document.edit_path))
        for name in ("intersect_model", "intersect_selection", "intersect_context"):
            a[name].setEnabled(bool(items))

    def delete_selection(self) -> None:
        """Erase the selected entities (one undoable command)."""
        items = self.viewport.selection.items()
        if not items:
            return
        active = self.document.active_entities
        self.viewport.selection.clear()
        self.document.perform("Erase", lambda: active.erase([e for e in items if e.parent is active]))

    def select_all(self) -> None:
        ents = self.document.active_entities
        self.viewport.selection.set([*ents.faces.values(), *ents.edges.values(), *ents.instances.values()])

    def select_none(self) -> None:
        self.viewport.selection.clear()

    # ------------------------------------------------------------------ groups & components
    def selected_in_context(self) -> list[Entity]:
        """Selected entities that belong to the context being edited."""
        active = self.document.active_entities
        return [e for e in self.viewport.selection.items() if e.parent is active]

    def _perform(self, name: str, action: Callable[[], object]) -> object | None:
        """Run an undoable command; geometry errors go to the status bar instead of raising."""
        try:
            return self.document.perform(name, action)
        except ValueError as exc:
            self.statusBar().showMessage(f"{name}: {exc}", 6000)
            return None

    def make_group(self) -> ComponentInstance | None:
        """Ctrl+G: group the selection."""
        return self._group_selection(component=False)

    def make_component(self) -> ComponentInstance | None:
        """G: make a component of the selection (asks for its name)."""
        if not self.selected_in_context():
            return None
        name = self.ask_text("Make Component", "Definition name:", self.document.model.unique_definition_name("Component"))
        if name is None:
            return None
        return self._group_selection(component=True, name=name)

    def _group_selection(self, component: bool, name: str = "") -> ComponentInstance | None:
        items = self.selected_in_context()
        if not items:
            self.statusBar().showMessage("Select something to group first", 4000)
            return None
        model, active = self.document.model, self.document.active_entities
        self.viewport.selection.clear()
        instance = self._perform("Make Component" if component else "Make Group",
                                 lambda: make_group(model, active, items, name=name, component=component))
        if isinstance(instance, ComponentInstance):
            self.viewport.selection.set([instance])
            return instance
        return None

    def edit_group(self) -> None:
        """Open the selected group or component for editing."""
        instances = [e for e in self.selected_in_context() if isinstance(e, ComponentInstance)]
        if len(instances) == 1:
            self.viewport.selection.clear()
            self.document.enter(instances[0])

    def close_group(self) -> None:
        """Close the innermost open group or component."""
        if self.document.edit_path:
            closed = self.document.edit_path[-1]
            self.document.exit()
            self.viewport.selection.set([closed])

    def explode_selection(self) -> None:
        """Replace the selected instances by their contents (selected afterwards)."""
        instances = [e for e in self.selected_in_context() if isinstance(e, ComponentInstance)]
        if not instances:
            return
        model, active = self.document.model, self.document.active_entities
        self.viewport.selection.clear()

        def action() -> list[Entity]:
            out: list[Entity] = []
            for inst in instances:
                out.extend(explode(model, active, inst))
            return out

        result = self._perform("Explode", action)
        if result:
            self.viewport.selection.set([e for e in result if e.alive and e.parent is active])

    def make_unique_selection(self) -> None:
        """Give each selected component instance its own definition."""
        instances = [e for e in self.selected_in_context()
                     if isinstance(e, ComponentInstance) and len(e.definition.instances) > 1]
        if instances:
            model = self.document.model
            self._perform("Make Unique", lambda: [make_unique(model, i) for i in instances])
            self.viewport.selection.set(instances)

    def intersect(self, mode: str = "model") -> int:
        """Intersect Faces with the model, the selection, or the context; returns new edge count."""
        items = self.selected_in_context()
        if not items:
            self.statusBar().showMessage("Select the faces or groups to intersect first", 5000)
            return 0
        doc = self.document
        mine = world_faces(items, doc.context_transform)
        own = {id(w.face) for w in mine}
        if mode == "selection":
            others = mine
        elif mode == "context":
            active = doc.active_entities
            others = [w for w in world_faces([*active.faces.values(), *active.instances.values()],
                                             doc.context_transform) if id(w.face) not in own]
        else:
            others = [WorldFace(f, placement.transform) for placement in doc.model.iter_placements()
                      for f in placement.entities.faces.values() if id(f) not in own]
        created = self._perform("Intersect Faces", lambda: intersect_faces(mine, others))
        count = len(created) if isinstance(created, list) else 0
        self.statusBar().showMessage(f"Intersect Faces: {count} new edge(s)", 5000)
        return count

    def hide_selection(self) -> None:
        items = self.selected_in_context()
        if items:
            self.viewport.selection.clear()
            self._perform("Hide", lambda: set_hidden(items, True))

    def unhide_all(self) -> None:
        active = self.document.active_entities
        hidden = [e for e in active.iter_entities() if e.hidden]
        if hidden:
            self._perform("Unhide All", lambda: set_hidden(hidden, False))

    def context_menu_actions(self) -> list[QAction | None]:
        """Actions of the viewport's right-click menu (``None`` is a separator)."""
        a = self.actions_by_name
        names: list[str | None] = ["delete", "hide", None, *ENTITY_ACTIONS, None, "select_all", "zoom_extents"]
        return [a[n] if n else None for n in names if n is None or a[n].isEnabled()]

    def show_context_menu(self, global_pos: QPoint, x: float, y: float) -> None:
        """SketchUp-style right-click: pick what is under the cursor, then show the menu."""
        entity = self.viewport.entity_at(x, y)
        if entity is not None and entity not in self.viewport.selection:
            self.viewport.selection.set([entity])
        menu = QMenu(self)
        for action in self.context_menu_actions():
            if action is None:
                if menu.actions() and not menu.actions()[-1].isSeparator():
                    menu.addSeparator()
            else:
                menu.addAction(action)
        self.exec_menu(menu, global_pos)

    def exec_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        """Show a popup menu modally (tests replace this)."""
        menu.exec(global_pos)

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

    def run_script(self, path: str | Path, keep_view: bool = False, live: bool = False) -> bool:
        """Build a script into the document (errors are shown, not raised).

        With ``live`` (a watched script was saved) errors appear in a banner over the view
        instead of a dialog, and the previous model stays on screen.
        """
        path = Path(path)
        try:
            result = self.document.run_script(path)
        except Exception as exc:  # noqa: BLE001
            if live:
                self.viewport.set_banner(f"Build failed: {path.name}: {self._describe(exc)}")
                self.statusBar().showMessage(f"Build failed: {path.name} (the previous model is still shown)", 8000)
            else:
                self.show_error(f"Build failed: {path.name}", self._describe(exc))
            return False
        self.viewport.set_banner("")
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

    # ------------------------------------------------------------------ live rebuild
    def start_watching(self, folder: str | Path) -> bool:
        """Rebuild scripts saved in ``folder`` (the newest one is opened if nothing is)."""
        folder = Path(folder)
        if not folder.is_dir():
            self.statusBar().showMessage(f"Cannot watch {folder}: no such folder", 6000)
            return False
        self.stop_watching()
        self.script_watcher = ScriptWatcher(folder, self.on_watched_script, parent=self)
        self.script_watcher.start()
        self.settings.setValue("watch_folder", str(folder.resolve()))
        self._sync_watch_ui()
        doc = self.document
        pristine = doc.path is None and doc.script_path is None and not doc.undo_stack.can_undo
        latest = self.script_watcher.watcher.latest()
        if pristine and latest is not None:
            self.run_script(latest, live=True)
        return True

    def stop_watching(self) -> None:
        if self.script_watcher is not None:
            self.script_watcher.stop()
            self.script_watcher.deleteLater()
            self.script_watcher = None
        self._sync_watch_ui()

    def set_live_rebuild(self, on: bool) -> None:
        """The File > Live Rebuild toggle."""
        if on and self.script_watcher is None:
            folder = self.settings.value("watch_folder", "")
            if not folder or not Path(str(folder)).is_dir() or not self.start_watching(str(folder)):
                self.choose_watch_folder()
        elif not on:
            self.stop_watching()
        self._sync_watch_ui()

    def choose_watch_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Watch Folder", str(self.settings.value("watch_folder", "")))
        if folder:
            self.start_watching(folder)

    def _sync_watch_ui(self) -> None:
        action = self.actions_by_name["live"]
        action.blockSignals(True)
        action.setChecked(self.script_watcher is not None)
        action.blockSignals(False)
        if self.script_watcher is not None:
            self.watch_label.setText(f"Live: {self.script_watcher.folder.name}/")
            self.watch_label.setToolTip(f"Rebuilding scripts saved in {self.script_watcher.folder.resolve()}")
        else:
            self.watch_label.setText("")

    def on_watched_script(self, path: Path) -> None:
        """A script in the watch folder was saved: show its new model.

        Hand edits since the last build or open would be lost, so the user is asked to
        save them first (skipping the rebuild if they cancel).
        """
        doc = self.document
        same = doc.script_path is not None and doc.script_path.resolve() == path.resolve()
        if doc.undo_stack.can_undo and doc.modified and not self.maybe_save():
            self.statusBar().showMessage(f"{path.name} changed; rebuild skipped to keep your edits", 8000)
            return
        if self.viewport.tool is not None:
            self.viewport.tool.reset()
        if self.run_script(path, keep_view=same, live=True):
            self.statusBar().showMessage(f"Rebuilt {path.name}", 4000)

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

    def set_guides(self, on: bool) -> None:
        self.viewport.set_show_guides(on)

    def delete_guides(self) -> None:
        """Erase all construction guides (one undoable command)."""
        model = self.document.model
        if model.guides:
            self._perform("Delete Guides", lambda: setattr(model, "guides", []))

    def set_axes(self, on: bool) -> None:
        self.viewport.show_axes = on
        self.viewport.refresh()

    def shortcut_rows(self) -> list[tuple[str, str]]:
        """(command, shortcut) pairs for every tool and every action with a shortcut."""
        rows = [(tool.name, tool.shortcut or "-") for tool in self.tools.values()]
        rows += [(action.text().replace("&", ""), action.shortcut().toString())
                 for action in self.actions_by_name.values() if not action.shortcut().isEmpty()]
        rows += [("Orbit / pan / zoom", "Middle-drag / Shift+middle-drag / wheel"),
                 ("Re-centre the view", "Double-click the middle button"),
                 ("Lock to red / green / blue axis", "Right / Left / Up arrow"),
                 ("Lock the current inference", "Shift (hold)"),
                 ("Copy (Move, Rotate) / keep face (Push/Pull) / guides on-off (Tape)", "Ctrl"),
                 ("Cancel / close the open group", "Esc")]
        return rows

    def show_shortcuts(self) -> None:
        rows = "".join(f"<tr><td>{name}</td><td><b>{key}</b></td></tr>" for name, key in self.shortcut_rows())
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard Shortcuts")
        box.setText(f"<table cellspacing='6'>{rows}</table>")
        self.exec_dialog(box)

    def exec_dialog(self, dialog: QDialog) -> int:
        """Show a modal dialog (tests replace this)."""
        return dialog.exec()

    def set_model_info(self, name: str, units: str) -> None:
        """Rename the model and change its display units (one undoable command)."""
        model = self.document.model
        units = normalize_unit(units)
        if (name, units) == (model.name, model.units):
            return

        def apply() -> None:
            model.name = name
            model.units = units

        self._perform("Model Info", apply)

    def model_info(self) -> None:
        """File > Model Info: the model's name and the units lengths are shown and typed in."""
        model = self.document.model
        dialog = QDialog(self)
        dialog.setWindowTitle("Model Info")
        form = QFormLayout(dialog)
        name = QLineEdit(model.name)
        units = QComboBox()
        units.addItems(list(MM_PER_UNIT))
        units.setCurrentText(model.units)
        form.addRow("Name", name)
        form.addRow("Units", units)
        form.addRow(QLabel("Units set how lengths are shown, and the default unit for typed values."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if self.exec_dialog(dialog) == QDialog.DialogCode.Accepted:
            self.set_model_info(name.text().strip(), units.currentText())

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

    def ask_text(self, title: str, label: str, default: str = "") -> str | None:
        """Ask for a line of text; ``None`` if cancelled (tests replace this)."""
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

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

