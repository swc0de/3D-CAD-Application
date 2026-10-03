"""The open document: a model, where it came from, and whether it has unsaved changes.

Kept free of Qt so it can be tested headless; the main window wires it to dialogs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pymodeler.core.model import Model
from pymodeler.io import export_model, load_model_file
from pymodeler.io.native import load_source
from pymodeler.script.engine import BuildResult, build_file

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

    # -- notifications ------------------------------------------------------------------
    def changed(self, modified: bool = True) -> None:
        """Mark the model as changed and notify listeners (e.g. the viewport)."""
        self.modified = self.modified or modified
        for listener in list(self.listeners):
            listener(self)

    @property
    def title(self) -> str:
        """Window title text."""
        name = self.path.name if self.path else (self.script_path.name if self.script_path else "Untitled")
        return f"{name}{' *' if self.modified else ''} - PyModeler"

    # -- file operations --------------------------------------------------------------------
    def new(self, units: str = "mm") -> None:
        """Start an empty model."""
        self.model = Model(units=units)
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
