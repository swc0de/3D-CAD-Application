"""Groups, components, explode, paint and tags."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from pymodeler.core.components import ComponentDefinition, ComponentInstance, add_instance, remove_instance
from pymodeler.core.entities import Edge, Entities, Entity, Face
from pymodeler.core.model import Model
from pymodeler.core.transform import identity, inverse, translation
from pymodeler.core.vec import GeometryError
from pymodeler.ops.transform import bounds_of, copy_geometry, split_targets


def make_group(
    model: Model,
    entities: Entities,
    targets: Sequence[Entity],
    name: str = "",
    component: bool = False,
    origin: np.ndarray | None = None,
) -> ComponentInstance:
    """Move ``targets`` out of ``entities`` into a new group (or component) instance.

    The geometry ends up exactly where it was.  Groups keep the parent's axes;
    components put their origin at ``origin`` (default: the bounding-box minimum).
    Every moved entity is recorded as replaced by the new instance, so references to
    the original geometry now point at the group.

    Raises:
        GeometryError: if there is nothing to group.
    """
    faces, edges, _, insts = split_targets(t for t in targets if t.parent is entities)
    if not (faces or edges or insts):
        raise GeometryError("nothing to group")
    if component and origin is None:
        box = bounds_of([*faces, *edges, *insts])
        origin = box[0] if box is not None else np.zeros(3)
    place = translation(origin) if origin is not None else identity()
    definition = model.add_definition(name or ("Component" if component else "Group"), is_group=not component)
    copy_geometry(entities, [*faces, *edges, *insts], definition.entities, inverse(place))
    instance = add_instance(entities, definition, place)
    instance.name = name if not component else ""
    moved_ids = [e.id for e in (*faces, *edges, *insts)]
    _remove_from_parent(entities, faces, edges, insts)
    for old in moved_ids:
        entities.registry.replaced(old, [instance.id])
    return instance


def _remove_from_parent(
    entities: Entities, faces: list[Face], edges: list[Edge], insts: list[ComponentInstance]
) -> None:
    """Erase grouped geometry, keeping edges that still bound other faces."""
    candidate_edges = {e for f in faces for e in f.edges()} | set(edges)
    entities.erase_faces(faces)
    strays = [e for e in candidate_edges if e.alive and not e.faces]
    entities.erase_edges(strays, heal=False)
    for inst in insts:
        remove_instance(entities, inst)


def explode(model: Model, entities: Entities, instance: ComponentInstance) -> list[Entity]:
    """Replace an instance by a copy of its contents (which then sticks to its new context).

    Faces without a material take the instance's material.  A group's definition is
    removed once its last instance is exploded.

    Returns:
        The entities created in ``entities``.
    """
    if instance.parent is not entities:
        raise GeometryError("the instance is not in this context")
    definition = instance.definition
    contents: list[Entity] = [
        *definition.entities.faces.values(),
        *definition.entities.edges.values(),
        *definition.entities.instances.values(),
    ]
    copied = copy_geometry(
        definition.entities, contents, entities, instance.transform, inherit_material=instance.material
    )
    for item in copied.entities:
        if instance.tag and getattr(item, "tag", None) is None:
            item.tag = instance.tag
    remove_instance(entities, instance)
    entities.registry.replaced(instance.id, [e.id for e in copied.entities])
    if definition.is_group and not definition.instances:
        model.remove_definition(definition)
    return copied.entities


def make_unique(model: Model, instance: ComponentInstance) -> ComponentDefinition:
    """Give ``instance`` its own copy of its definition so edits affect only it.

    Does nothing (and returns the current definition) if it is the only instance.
    """
    old = instance.definition
    if len(old.instances) <= 1:
        return old
    new = model.add_definition(old.name, is_group=old.is_group)
    new.description = old.description
    contents: list[Entity] = [*old.entities.faces.values(), *old.entities.edges.values(),
                              *old.entities.instances.values()]
    copy_geometry(old.entities, contents, new.entities, identity())
    old.instances.remove(instance)
    instance.definition = new
    new.instances.append(instance)
    return new


def place_component(
    entities: Entities, definition: ComponentDefinition, transform: np.ndarray, name: str = ""
) -> ComponentInstance:
    """Place an instance of ``definition``."""
    instance = add_instance(entities, definition, transform)
    instance.name = name
    return instance


def paint(model: Model, targets: Sequence[Entity], material: str | None, side: str = "front") -> int:
    """Apply ``material`` (``None`` resets to default) to faces and instances.

    Args:
        side: ``"front"``, ``"back"`` or ``"both"`` (faces only).

    Returns:
        Number of entities painted.

    Raises:
        GeometryError: on an unknown material or side.
    """
    if material is not None and material not in model.materials:
        raise GeometryError(f"unknown material {material!r}")
    if side not in ("front", "back", "both"):
        raise GeometryError("side must be front, back or both")
    count = 0
    for t in targets:
        if isinstance(t, Face):
            if side in ("front", "both"):
                t.material = material
            if side in ("back", "both"):
                t.back_material = material
        elif isinstance(t, ComponentInstance):
            t.material = material
        else:
            continue
        count += 1
        if t.parent is not None:
            t.parent.registry.modified(t.id)
    return count


def set_tag(model: Model, targets: Sequence[Entity], tag: str | None) -> int:
    """Put entities on ``tag`` (created if new); ``None`` means Untagged."""
    if tag is not None:
        model.add_tag(tag)
    for t in targets:
        t.tag = tag
    return len(targets)


def set_hidden(targets: Sequence[Entity], hidden: bool) -> int:
    """Hide or unhide entities."""
    for t in targets:
        t.hidden = hidden
    return len(targets)
