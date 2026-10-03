"""Snapshot-based undo/redo (no Qt dependency).

Every command records the model as it was before the command (``.pym`` data in
memory). Undo restores that snapshot; redo restores the state that undo replaced.
Simple and robust: any operation becomes undoable without per-op inverse logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Snapshot = dict[str, Any]


@dataclass
class UndoStack:
    """Named snapshots for undo and redo."""

    limit: int = 100
    _undo: list[tuple[str, Snapshot]] = field(default_factory=list)
    _redo: list[tuple[str, Snapshot]] = field(default_factory=list)

    def push(self, name: str, before: Snapshot) -> None:
        """Record a finished command and the state before it (clears redo)."""
        self._undo.append((name, before))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def clear(self) -> None:
        """Forget all history (e.g. after opening another file)."""
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_name(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    @property
    def redo_name(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self, current: Snapshot) -> Snapshot:
        """Snapshot to restore for undo; ``current`` becomes redoable."""
        name, before = self._undo.pop()
        self._redo.append((name, current))
        return before

    def redo(self, current: Snapshot) -> Snapshot:
        """Snapshot to restore for redo; ``current`` becomes undoable again."""
        name, after = self._redo.pop()
        self._undo.append((name, current))
        return after
