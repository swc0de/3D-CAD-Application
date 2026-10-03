"""Entity id allocation and change tracking.

Every entity in a model gets a unique integer id from a shared :class:`Registry`.
Operations open a :class:`ChangeLog` so callers (the build-script engine, the GUI's
selection) can learn which entities were created, erased, split or merged, and keep
named references pointing at the right geometry.
"""

from __future__ import annotations

from typing import Any, Iterator


class ChangeLog:
    """Records entity lifecycle events during an operation."""

    def __init__(self) -> None:
        self.created: dict[int, None] = {}
        self.erased: dict[int, None] = {}
        self.replaced: dict[int, list[int]] = {}
        self.modified: dict[int, None] = {}

    def record_created(self, entity_id: int) -> None:
        """Note that an entity was created."""
        self.created[entity_id] = None

    def record_erased(self, entity_id: int) -> None:
        """Note that an entity was erased."""
        self.erased[entity_id] = None

    def record_modified(self, entity_id: int) -> None:
        """Note that an entity's geometry or attributes changed."""
        self.modified[entity_id] = None

    def record_replaced(self, old_id: int, new_ids: list[int]) -> None:
        """Note that ``old_id`` is now represented by ``new_ids`` (split or merge)."""
        bucket = self.replaced.setdefault(old_id, [])
        for nid in new_ids:
            if nid not in bucket:
                bucket.append(nid)

    def successors(self, entity_id: int, registry: "Registry") -> list[int]:
        """Ids of live entities that now stand for ``entity_id``.

        Follows split/merge chains.  An entity that still exists maps to itself (plus
        any pieces split off it); an erased one maps to whatever replaced it, if anything.
        """
        out: list[int] = []
        seen: set[int] = set()
        stack = [entity_id]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            nexts = self.replaced.get(current)
            if nexts:
                stack.extend(reversed([n for n in nexts if n != current]))
            if registry.get(current) is not None and current not in out:
                out.append(current)
        return out

    def net_created(self, registry: "Registry") -> list[int]:
        """Ids created during the log that are still alive."""
        return [i for i in self.created if registry.get(i) is not None]


class Registry:
    """Shared per-model id allocator, entity lookup and change-log stack."""

    def __init__(self) -> None:
        self._next_id = 1
        self._objects: dict[int, Any] = {}
        self._logs: list[ChangeLog] = []

    def allocate(self, obj: Any) -> int:
        """Register ``obj`` and return its new id."""
        entity_id = self._next_id
        self._next_id += 1
        self._objects[entity_id] = obj
        for log in self._logs:
            log.record_created(entity_id)
        return entity_id

    def adopt(self, obj: Any, entity_id: int) -> None:
        """Register ``obj`` under an existing id (used when loading files)."""
        self._objects[entity_id] = obj
        self._next_id = max(self._next_id, entity_id + 1)

    def release(self, entity_id: int) -> None:
        """Forget an erased entity."""
        self._objects.pop(entity_id, None)
        for log in self._logs:
            log.record_erased(entity_id)

    def get(self, entity_id: int) -> Any | None:
        """Look up a live entity by id."""
        return self._objects.get(entity_id)

    def new_id(self) -> int:
        """Allocate a bare id that is not tied to an object (e.g. curve ids)."""
        entity_id = self._next_id
        self._next_id += 1
        return entity_id

    def replaced(self, old_id: int, new_ids: list[int]) -> None:
        """Broadcast a split/merge event to every open log."""
        for log in self._logs:
            log.record_replaced(old_id, new_ids)

    def modified(self, entity_id: int) -> None:
        """Broadcast a modification event to every open log."""
        for log in self._logs:
            log.record_modified(entity_id)

    def begin(self) -> ChangeLog:
        """Open a new change log; events go to every open log until :meth:`end`."""
        log = ChangeLog()
        self._logs.append(log)
        return log

    def end(self, log: ChangeLog) -> None:
        """Close a change log opened by :meth:`begin`."""
        if log in self._logs:
            self._logs.remove(log)

    def tracking(self) -> "_Tracking":
        """Context manager form of :meth:`begin` / :meth:`end`."""
        return _Tracking(self)

    def __iter__(self) -> Iterator[int]:
        return iter(list(self._objects))


class _Tracking:
    """``with registry.tracking() as log:`` helper."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self._log: ChangeLog | None = None

    def __enter__(self) -> ChangeLog:
        self._log = self._registry.begin()
        return self._log

    def __exit__(self, *exc: object) -> None:
        assert self._log is not None
        self._registry.end(self._log)
