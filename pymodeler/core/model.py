"""The :class:`Model`: root geometry, component definitions, materials and tags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from pymodeler.core.changes import Registry
from pymodeler.core.components import ComponentDefinition, ComponentInstance, entities_bounds
from pymodeler.core.entities import Entities, Entity
from pymodeler.core.materials import Material
from pymodeler.core.tags import UNTAGGED, Tag
from pymodeler.core.transform import identity
from pymodeler.core.units import normalize_unit


@dataclass
class Placement:
    """One collection of geometry as it appears in the world.

    Produced by :meth:`Model.iter_placements` while walking the instance tree.
    """

    entities: Entities
    transform: np.ndarray
    """Local-to-world transform of the collection."""
    material: str | None
    """Material inherited from enclosing instances (applies to unpainted faces)."""
    path: tuple[ComponentInstance, ...]
    """Instances from the root down to this collection."""


class Model:
    """A complete 3D model.

    Attributes:
        entities: Root geometry and top-level instances.
        definitions: Component (and group) definitions by name.
        materials: Materials by name.
        tags: Tags by name (always contains ``"Untagged"``).
        units: Display unit for the model (``mm``, ``cm``, ``m``, ``in`` or ``ft``).
    """

    def __init__(self, units: str = "mm") -> None:
        self.registry = Registry()
        self.entities = Entities(self.registry, owner=self)
        self.definitions: dict[str, ComponentDefinition] = {}
        self.materials: dict[str, Material] = {}
        self.tags: dict[str, Tag] = {UNTAGGED: Tag(UNTAGGED)}
        self.units = normalize_unit(units)
        self.name = ""
        self.description = ""

    # -- definitions ------------------------------------------------------------------
    def unique_definition_name(self, base: str) -> str:
        """``base`` if unused, otherwise ``base#2``, ``base#3``..."""
        if base not in self.definitions:
            return base
        n = 2
        while f"{base}#{n}" in self.definitions:
            n += 1
        return f"{base}#{n}"

    def add_definition(self, name: str, is_group: bool = False) -> ComponentDefinition:
        """Create an empty definition with a unique name derived from ``name``."""
        definition = ComponentDefinition(self.registry, self.unique_definition_name(name), is_group)
        self.definitions[definition.name] = definition
        return definition

    def remove_definition(self, definition: ComponentDefinition) -> None:
        """Delete a definition that has no instances left."""
        if definition.instances:
            raise ValueError(f"definition {definition.name!r} still has instances")
        self.definitions.pop(definition.name, None)
        self.registry.release(definition.id)

    def purge_unused_definitions(self) -> int:
        """Remove definitions with no instances; returns how many were removed."""
        unused = [d for d in self.definitions.values() if not d.instances]
        for d in unused:
            self.remove_definition(d)
        return len(unused)

    # -- materials & tags -----------------------------------------------------------
    def add_material(self, material: Material) -> Material:
        """Add or replace a material."""
        self.materials[material.name] = material
        return material

    def add_tag(self, name: str, visible: bool = True) -> Tag:
        """Return the tag called ``name``, creating it if needed."""
        if name not in self.tags:
            self.tags[name] = Tag(name, visible)
        return self.tags[name]

    def is_tag_visible(self, name: str | None) -> bool:
        """True unless ``name`` refers to a hidden tag."""
        if name is None:
            return True
        tag = self.tags.get(name)
        return tag.visible if tag is not None else True

    # -- lookup & traversal ---------------------------------------------------------
    def lookup(self, entity_id: int) -> Entity | ComponentDefinition | None:
        """Find a live entity or definition by id."""
        return self.registry.get(entity_id)

    def iter_placements(self, visible_only: bool = True) -> Iterator[Placement]:
        """Walk the instance tree, yielding every collection with its world transform.

        Hidden instances and instances on hidden tags (and everything inside them) are
        skipped when ``visible_only`` is set.
        """
        yield from self._walk(self.entities, identity(), None, (), visible_only)

    def _walk(
        self,
        entities: Entities,
        transform: np.ndarray,
        material: str | None,
        path: tuple[ComponentInstance, ...],
        visible_only: bool,
    ) -> Iterator[Placement]:
        yield Placement(entities, transform, material, path)
        if len(path) > 64:
            return
        for inst in entities.instances.values():
            if visible_only and (inst.hidden or not self.is_tag_visible(inst.tag)):
                continue
            yield from self._walk(
                inst.definition.entities,
                transform @ inst.transform,
                inst.material or material,
                path + (inst,),
                visible_only,
            )

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """World bounding box of everything in the model, or ``None`` if empty."""
        return entities_bounds(self.entities, identity())

    def stats(self) -> dict[str, int]:
        """Counts of raw entities across all definitions plus the root."""
        collections = [self.entities] + [d.entities for d in self.definitions.values()]
        return {
            "faces": sum(len(c.faces) for c in collections),
            "edges": sum(len(c.edges) for c in collections),
            "vertices": sum(len(c.vertices) for c in collections),
            "instances": sum(len(c.instances) for c in collections),
            "definitions": len(self.definitions),
        }
