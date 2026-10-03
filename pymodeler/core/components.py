"""Component definitions and instances (groups are single-use components)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from pymodeler.core.entities import Entities, Entity
from pymodeler.core.transform import apply_points, identity

if TYPE_CHECKING:
    from pymodeler.core.changes import Registry


class ComponentDefinition:
    """Shared geometry that can be placed many times through instances.

    A *group* is a definition with ``is_group=True`` that is meant to have exactly one
    instance; editing it never affects other geometry.
    """

    def __init__(self, registry: "Registry", name: str, is_group: bool = False) -> None:
        self.id = registry.allocate(self)
        self.name = name
        self.is_group = is_group
        self.description = ""
        self.entities = Entities(registry, owner=self)
        self.instances: list[ComponentInstance] = []

    def __repr__(self) -> str:
        kind = "Group" if self.is_group else "Component"
        return f"<{kind}Definition {self.name!r} #{self.id}>"

    def local_bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Bounding box of the definition's contents in its own coordinates."""
        return entities_bounds(self.entities, identity())


class ComponentInstance(Entity):
    """A placement of a :class:`ComponentDefinition` with its own transform."""

    def __init__(
        self, parent: Entities, definition: ComponentDefinition, transform: np.ndarray
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.transform = np.array(transform, dtype=np.float64)
        self.name = ""
        self.material: str | None = None
        self.locked = False
        definition.instances.append(self)

    @property
    def is_group(self) -> bool:
        """True if this instance is a group."""
        return self.definition.is_group

    @property
    def display_name(self) -> str:
        """Instance name, falling back to the definition name."""
        return self.name or self.definition.name

    def __repr__(self) -> str:
        return f"<Instance of {self.definition.name!r} #{self.id}>"


def add_instance(
    entities: Entities, definition: ComponentDefinition, transform: np.ndarray | None = None
) -> ComponentInstance:
    """Place ``definition`` into ``entities``."""
    inst = ComponentInstance(entities, definition, identity() if transform is None else transform)
    entities.instances[inst.id] = inst
    return inst


def remove_instance(entities: Entities, inst: ComponentInstance) -> None:
    """Erase an instance (its definition is kept)."""
    if inst.parent is not entities:
        return
    entities.instances.pop(inst.id, None)
    if inst in inst.definition.instances:
        inst.definition.instances.remove(inst)
    entities.registry.release(inst.id)
    inst.parent = None


def entities_bounds(
    entities: Entities, transform: np.ndarray, _depth: int = 0
) -> tuple[np.ndarray, np.ndarray] | None:
    """World bounding box of a collection (including nested instances) under ``transform``."""
    boxes: list[np.ndarray] = []
    if entities.vertices:
        pts = np.array([v._t for v in entities.vertices.values()])
        boxes.append(apply_points(transform, pts))
    if _depth < 64:
        for inst in entities.instances.values():
            sub = entities_bounds(inst.definition.entities, transform @ inst.transform, _depth + 1)
            if sub is not None:
                boxes.append(np.array([sub[0], sub[1]]))
    if not boxes:
        return None
    allpts = np.vstack(boxes)
    return allpts.min(axis=0), allpts.max(axis=0)

