"""A slot-based bounding-box index with vectorised (numpy) queries.

The sticky-geometry kernel asks "which edges/vertices are near this segment?" and
"which vertices lie on this plane?" many times per operation.  Keeping every item's
box in contiguous numpy arrays turns those scans into a few vectorised comparisons.
"""

from __future__ import annotations

from typing import Generic, Sequence, TypeVar

import numpy as np

T = TypeVar("T")


class BoxIndex(Generic[T]):
    """Axis-aligned boxes stored in growable arrays, addressed by integer slots."""

    def __init__(self, capacity: int = 64) -> None:
        self._lo = np.zeros((capacity, 3))
        self._hi = np.zeros((capacity, 3))
        self._alive = np.zeros(capacity, dtype=bool)
        self._items: list[T | None] = [None] * capacity
        self._free: list[int] = []
        self._used = 0

    def add(self, item: T, lo: Sequence[float], hi: Sequence[float]) -> int:
        """Store ``item`` with box ``lo..hi``; returns its slot."""
        if self._free:
            slot = self._free.pop()
        else:
            if self._used == len(self._items):
                self._grow()
            slot = self._used
            self._used += 1
        self._lo[slot] = lo
        self._hi[slot] = hi
        self._alive[slot] = True
        self._items[slot] = item
        return slot

    def update(self, slot: int, lo: Sequence[float], hi: Sequence[float]) -> None:
        """Change the box of an existing slot."""
        self._lo[slot] = lo
        self._hi[slot] = hi

    def remove(self, slot: int) -> None:
        """Free a slot."""
        self._alive[slot] = False
        self._items[slot] = None
        self._free.append(slot)

    def query(self, lo: Sequence[float], hi: Sequence[float]) -> list[T]:
        """Items whose boxes overlap ``lo..hi`` (inclusive), in slot order."""
        n = self._used
        if n == 0:
            return []
        mask = self._alive[:n].copy()
        mask &= np.all(self._lo[:n] <= np.asarray(hi), axis=1)
        mask &= np.all(self._hi[:n] >= np.asarray(lo), axis=1)
        return [self._items[i] for i in np.flatnonzero(mask)]  # type: ignore[misc]

    def query_plane(self, normal: np.ndarray, offset: float, tol: float) -> list[T]:
        """Items whose box *minimum corner* lies within ``tol`` of a plane.

        Intended for point items (vertices), whose boxes are degenerate.
        """
        n = self._used
        if n == 0:
            return []
        dist = self._lo[:n] @ normal - offset
        mask = self._alive[:n] & (np.abs(dist) <= tol)
        return [self._items[i] for i in np.flatnonzero(mask)]  # type: ignore[misc]

    def _grow(self) -> None:
        """Double the capacity."""
        cap = len(self._items) * 2
        self._lo = np.resize(self._lo, (cap, 3))
        self._hi = np.resize(self._hi, (cap, 3))
        alive = np.zeros(cap, dtype=bool)
        alive[: len(self._alive)] = self._alive
        self._alive = alive
        self._items.extend([None] * (cap - len(self._items)))
