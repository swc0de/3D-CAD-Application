"""The open document: a model, where it came from, and whether it has unsaved changes.

Kept free of Qt so it can be tested headless; the main window wires it to dialogs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pymodeler.core.components import ComponentInstance
from pymodeler.core.entities import Entities
from pymodeler.core.model import Model
from pymodeler.core.transform import apply_point, apply_vector, identity, inverse
from pymodeler.io import export_model, load_model_file
from pymodeler.io.native import load_source, model_from_dict, model_to_dict
from pymodeler.ops.organize import make_unique
from pymodeler.script.engine import BuildResult, build_file
from pymodeler.ui.undo import UndoStack

OPENABLE = (".pym", ".obj", ".json")
MAX_RECENT = 8


@dataclass
class Document:
    """State of the model being edited."""

    model: Model = field(default_factory=Model)
    path: Path | None = None
    """File the document is saved to (``None`` until first saved)."""
    script_path: Path | None = None
    """Build script the model was produced from, if any."""
    source: dict[str, Any] | None = None
    """The build script itself (embedded into saved .pym files)."""
    modified: bool = False
    listeners: list[Callable[["Document"], None]] = field(default_factory=list)
    last_build: BuildResult | None = None
    undo_stack: UndoStack = field(default_factory=UndoStack)
    version: int = 0
    """Incremented on every change (lets views cache derived data)."""
    edit_path: list[ComponentInstance] = field(default_factory=list)
    """Groups/components being edited, outermost first (empty: editing the model root)."""

    # -- notifications ------------------------------------------------------------------
    def changed(self, modified: bool = True) -> None:
        """Mark the model as changed and notify listeners (e.g. the viewport)."""
        self.modified = self.modified or modified
        self.version += 1
        for listener in list(self.listeners):
            listener(self)

    @property
    def active_entities(self) -> Entities:
        """The collection being edited: the model root, or the open group/component."""
        return self.edit_path[-1].definition.entities if self.edit_path else self.model.entities

    # -- editing context (inside groups) -------------------------------------------------------
    @property
    def context_transform(self) -> np.ndarray:
        """Transform from the active collection's coordinates to world coordinates."""
        m = identity()
        for inst in self.edit_path:
            m = m @ inst.transform
        return m

    def to_local(self, point: np.ndarray) -> np.ndarray:
        """A world point in the active collection's coordinates."""
        return apply_point(inverse(self.context_transform), point)

    def to_local_vector(self, vector: np.ndarray) -> np.ndarray:
        """A world direction in the active collection's coordinates."""
        return apply_vector(inverse(self.context_transform), vector)

    def to_local_matrix(self, matrix: np.ndarray) -> np.ndarray:
        """A world-space transform expressed in the active collection's coordinates."""
        ct = self.context_transform
        return inverse(ct) @ matrix @ ct

    def to_world(self, point: np.ndarray) -> np.ndarray:
        """A point of the active collection in world coordinates."""
        return apply_point(self.context_transform, point)

    def enter(self, instance: ComponentInstance) -> None:
        """Open a group or component in the active collection for editing."""
        if instance.parent is not self.active_entities:
            raise ValueError("only groups in the current context can be opened")
        if instance.is_group and len(instance.definition.instances) > 1:
            # Copied groups share their contents until edited (as in SketchUp); opening
            # one gives it its own copy so the other copies stay as they are.
            make_unique(self.model, instance)
        self.edit_path.append(instance)
        self.changed(modified=False)

    def exit(self) -> bool:
        """Close the innermost open group; returns False when already at the root."""
        if not self.edit_path:
            return False
        self.edit_path.pop()
        self.changed(modified=False)
        return True

    def _path_indices(self) -> list[int]:
        out = []
        parent = self.model.entities
        for inst in self.edit_path:
            ids = list(parent.instances)
            if inst.id not in ids:
                break
            out.append(ids.index(inst.id))
            parent = inst.definition.entities
        return out

    def _resolve_path(self, indices: list[int]) -> list[ComponentInstance]:
        path: list[ComponentInstance] = []
        parent = self.model.entities
        for index in indices:
            instances = list(parent.instances.values())
            if index >= len(instances):
                break
            path.append(instances[index])
            parent = instances[index].definition.entities
        return path

    # -- undoable editing ---------------------------------------------------------------------
    def perform(self, name: str, action: Callable[[], Any]) -> Any:
        """Run ``action`` as one undoable command.

        If the action raises, the model is restored and the error propagates.
        """
        before = model_to_dict(self.model)
        try:
            result = action()
        except Exception:
            self._restore(before)
            raise
        self.undo_stack.push(name, before)
        self.changed()
        return result

    def undo(self) -> bool:
        """Undo the last command; returns False if there is nothing to undo."""
        if not self.undo_stack.can_undo:
            return False
        self._restore(self.undo_stack.undo(model_to_dict(self.model)))
        self.changed()
        return True

    def redo(self) -> bool:
        """Redo the last undone command."""
        if not self.undo_stack.can_redo:
            return False
        self._restore(self.undo_stack.redo(model_to_dict(self.model)))
        self.changed()
        return True

    def _restore(self, snapshot: dict[str, Any]) -> None:
        indices = self._path_indices()
        self.model = model_from_dict(snapshot)
        self.edit_path = self._resolve_path(indices)

    @property
    def title(self) -> str:
        """Window title text."""
        name = self.path.name if self.path else (self.script_path.name if self.script_path else "Untitled")
        return f"{name}{' *' if self.modified else ''} - PyModeler"

    # -- file operations --------------------------------------------------------------------
    def new(self, units: str = "mm") -> None:
        """Start an empty model."""
        self.model = Model(units=units)
        self.undo_stack.clear()
        self.edit_path = []
        self.path = self.script_path = None
        self.source = None
        self.last_build = None
        self.modified = False
        self.changed(modified=False)

    def open(self, path: str | Path) -> None:
        """Open a .pym model, import an .obj, or build a .json script.

        Raises:
            ValueError / OSError / ScriptError: with a readable message on failure.
        """
        path = Path(path)
        if path.suffix.lower() == ".json":
            self.run_script(path)
            return
        model = load_model_file(path)
        self.model = model
        self.undo_stack.clear()
        self.edit_path = []
        self.path = path if path.suffix.lower() == ".pym" else None
        self.source = load_source(path) if path.suffix.lower() == ".pym" else None
        self.script_path = None
        self.last_build = None
        self.modified = path.suffix.lower() != ".pym"
        self.changed(modified=self.modified)

    def run_script(self, path: str | Path) -> BuildResult:
        """Build a script and make its model the document's model.

        The document stays unchanged if the build fails (the error propagates).
        """
        path = Path(path)
        result = build_file(path)
        self.model = result.model
        self.undo_stack.clear()
        self.edit_path = []
        self.last_build = result
        self.script_path = path
        self.source = result.script
        self.path = None
        self.modified = True
        self.changed()
        return result

    def save(self, path: str | Path | None = None) -> Path:
        """Save as .pym (to ``path`` or the current path).

        Raises:
            ValueError: if there is no path to save to.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("choose a file name first (Save As)")
        if target.suffix.lower() != ".pym":
            target = target.with_suffix(".pym")
        export_model(self.model, target, self.source)
        self.path = target
        self.modified = False
        self.changed(modified=False)
        return target

    def export(self, path: str | Path) -> None:
        """Export to OBJ/STL/glTF/GLB (or another .pym) without changing the save path."""
        export_model(self.model, path, self.source if str(path).lower().endswith(".pym") else None)


def push_recent(recent: list[str], path: str | Path) -> list[str]:
    """Most-recent-first list with ``path`` moved to the front."""
    text = str(Path(path).resolve())
    out = [text] + [p for p in recent if p != text]
    return out[:MAX_RECENT]
