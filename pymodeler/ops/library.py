"""Every modeling operation, registered with typed parameters.

These bindings adapt the geometric functions in :mod:`pymodeler.ops` to the registry.
Arguments arrive already converted: lengths in millimetres, points and vectors in
*step coordinates* (``ctx.pt`` / ``ctx.vec`` map them into the active collection),
targets as :class:`~pymodeler.ops.registry.Target`.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from pymodeler.core.components import ComponentDefinition, ComponentInstance, add_instance, context_world, entities_bounds
from pymodeler.core.entities import Edge, Entities, Entity, Face, Vertex
from pymodeler.core.transform import (
    apply_point,
    apply_points,
    identity,
    inverse,
    rotation,
    scaling,
    translation,
)
from pymodeler.core.vec import GeometryError, normalize
from pymodeler.ops import draw, primitives
from pymodeler.ops.edit import WorldFace, intersect_faces
from pymodeler.ops.extrude import extrude, follow_me, offset_face, path_from_edges, push_pull
from pymodeler.ops.organize import explode, make_group, paint, place_component, set_hidden, set_tag
from pymodeler.ops.registry import OpContext, OpOutput, Param, Target, op
from pymodeler.ops.selectors import Picked, candidate_faces, select_faces
from pymodeler.ops.transform import copy_geometry, transform_entities

# ---------------------------------------------------------------------- shared params

TARGET = Param("target", "target", "what to operate on", required=True)
FACE = Param("face", "face", "which face(s) of the target; may be omitted when the target is a single face")
PLANE = Param("plane", "enum", "drawing plane: xy (ground, faces +Z), xz (faces front, -Y), yz (faces right, +X)",
              default="xy", choices=("xy", "xz", "yz"))
NORMAL = Param("normal", "direction", "front direction of the shape; overrides 'plane'")
X_AXIS = Param("x_axis", "direction", "direction of the shape's local X axis within its plane")
GROUP = Param("group", "bool", "wrap the solid in its own group so it never sticks to other geometry", default=True)
NAME = Param("name", "string", "name shown in the outliner (also usable as a reference)")
SEGMENTS = Param("segments", "integer", "number of straight segments approximating the curve", default=24)


def _axes(ctx: OpContext, a: dict[str, Any]) -> draw.PlaneAxes:
    """Drawing axes from plane/normal/x_axis, mapped into the active collection."""
    return ctx.axes(draw.plane_axes(a.get("plane"), a.get("normal"), a.get("x_axis")))


def _world_of(entity: Entity) -> np.ndarray:
    """World transform of an entity's collection."""
    assert entity.parent is not None
    return context_world(entity.parent)


def _by_context(entities: list[Entity]) -> dict[int, tuple[Entities, list[Entity]]]:
    """Group live entities by the collection they belong to."""
    out: dict[int, tuple[Entities, list[Entity]]] = {}
    for e in entities:
        if e.parent is not None:
            out.setdefault(id(e.parent), (e.parent, []))[1].append(e)
    return out


def target_bounds(entities: list[Entity]) -> tuple[np.ndarray, np.ndarray] | None:
    """World bounding box of a mix of faces, edges, vertices and instances."""
    pts: list[np.ndarray] = []
    for e in entities:
        if e.parent is None:
            continue
        world = _world_of(e)
        if isinstance(e, Face):
            pts.extend(apply_points(world, np.array([v._t for v in e.vertices()])))
        elif isinstance(e, Edge):
            pts.extend(apply_points(world, np.array([e.v1._t, e.v2._t])))
        elif isinstance(e, Vertex):
            pts.append(apply_point(world, e.position))
        elif isinstance(e, ComponentInstance):
            box = entities_bounds(e.definition.entities, world @ e.transform)
            if box is not None:
                pts.extend(box)
    if not pts:
        return None
    arr = np.array(pts)
    return arr.min(axis=0), arr.max(axis=0)


def _anchor(ctx: OpContext, value: Any, entities: list[Entity]) -> np.ndarray:
    """World point for an anchor parameter (a point or a bounding-box keyword)."""
    if isinstance(value, np.ndarray):
        return ctx.world_point(value)
    box = target_bounds(entities)
    if box is None:
        raise GeometryError("the target has no geometry to anchor to")
    lo, hi = box
    mid = (lo + hi) / 2
    table = {
        "center": mid,
        "min": lo,
        "max": hi,
        "bottom": np.array([mid[0], mid[1], lo[2]]),
        "top": np.array([mid[0], mid[1], hi[2]]),
    }
    return table[value]


def _apply_world(entities: list[Entity], world_matrix: np.ndarray) -> None:
    """Transform entities (in any collections) by a world-space matrix."""
    for ents, items in _by_context(entities).values():
        w = context_world(ents)
        transform_entities(ents, items, inverse(w) @ world_matrix @ w)


def _copy_world(ctx: OpContext, entities: list[Entity], world_matrix: np.ndarray) -> list[Entity]:
    """Copy entities by a world-space matrix; group copies get their own definitions."""
    out: list[Entity] = []
    for ents, items in _by_context(entities).values():
        w = context_world(ents)
        copied = copy_geometry(ents, items, ents, inverse(w) @ world_matrix @ w)
        for inst in copied.instances:
            if inst.definition.is_group:
                _make_unique(ctx, inst)
        out.extend(copied.entities)
    return out


def _make_unique(ctx: OpContext, inst: ComponentInstance) -> None:
    """Give a group instance its own copy of its definition."""
    old = inst.definition
    new = ctx.model.add_definition(old.name, is_group=True)
    contents: list[Entity] = [*old.entities.faces.values(), *old.entities.edges.values(), *old.entities.instances.values()]
    copy_geometry(old.entities, contents, new.entities, identity())
    old.instances.remove(inst)
    inst.definition = new
    new.instances.append(inst)


def _items(a: dict[str, Any]) -> list[Entity]:
    """Entities a transform acts on: the selected faces when 'face' is given, else the target."""
    if a.get("face") is None:
        return a["target"].all()
    return [p.face for part in a["target"].parts for p in _picks(part, a["face"])]


def _picks(part: list[Entity], selector: Any) -> list[Picked]:
    """Faces selected from one target part."""
    return select_faces(candidate_faces(part), selector)


def _local_length(p: Picked, length: float) -> float:
    """Convert a world length along a picked face's normal to its collection's units."""
    stretch = float(np.linalg.norm(p.world[:3, :3] @ p.face.normal))
    return length / max(stretch, 1e-12)


def _grouped(
    ctx: OpContext, a: dict[str, Any], default_name: str, build: Callable[[Entities], Any]
) -> list[Entity]:
    """Build geometry in step coordinates, inside a new group unless ``group`` is false."""
    if a.get("group", True):
        name = a.get("name") or default_name
        definition = ctx.model.add_definition(name, is_group=True)
        inst = add_instance(ctx.entities, definition, ctx.to_active)
        inst.name = a.get("name") or ""
        build(definition.entities)
        return [inst]
    scratch = Entities()
    build(scratch)
    items: list[Entity] = [*scratch.faces.values(), *scratch.edges.values()]
    return copy_geometry(scratch, items, ctx.entities, ctx.to_active).entities


def _drawn(result: draw.Drawn) -> OpOutput:
    """Output for a drawing op."""
    return OpOutput.single(result.entities)


# ====================================================================== draw


@op("line", "draw", "Draw connected straight edges. A closed, flat loop becomes a face.",
    [Param("points", "points", "two or more points to connect in order"),
     Param("from", "point", "start point (use with 'to')"),
     Param("to", "point", "end point (use with 'from')"),
     Param("closed", "bool", "also connect the last point back to the first", default=False)],
    one_of=[("points", "from")],
    example={"op": "line", "id": "outline", "points": [[0, 0, 0], [2000, 0, 0], [2000, 1000, 0]], "closed": True})
def _line(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    if a.get("points") is not None:
        pts = a["points"]
    elif a.get("from") is not None and a.get("to") is not None:
        pts = [a["from"], a["to"]]
    else:
        raise GeometryError("line needs 'points', or both 'from' and 'to'")
    return _drawn(draw.line(ctx.entities, [ctx.pt(p) for p in pts], closed=a["closed"]))


@op("rectangle", "draw", "Draw a rectangular face from a corner (or its centre).",
    [Param("origin", "point", "corner of the rectangle (its centre if center is true)", default=[0, 0, 0]),
     Param("width", "length", "size along the plane's first axis (X for xy and xz, Y for yz)", required=True),
     Param("depth", "length", "size along the plane's second axis (Y for xy, Z for xz and yz)",
           required=True, aliases=("height",)),
     PLANE, NORMAL, X_AXIS,
     Param("center", "bool", "treat origin as the centre", default=False)],
    example={"op": "rectangle", "id": "floor", "origin": [0, 0, 0], "width": "$w", "depth": "$d"})
def _rectangle(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    r = draw.rectangle(ctx.entities, ctx.pt(a["origin"]), a["width"], a["depth"], _axes(ctx, a), centered=a["center"])
    return _drawn(r)


@op("circle", "draw", "Draw a circular face (a polygon with many sides, marked as one curve).",
    [Param("center", "point", "centre point", default=[0, 0, 0]),
     Param("radius", "length", "radius", required=True),
     SEGMENTS, PLANE, NORMAL],
    example={"op": "circle", "id": "disc", "center": [0, 0, 0], "radius": 500, "segments": 32})
def _circle(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    return _drawn(draw.circle(ctx.entities, ctx.pt(a["center"]), a["radius"], a["segments"], _axes(ctx, a)))


@op("arc", "draw", "Draw an arc of a circle; optionally close it into a face.",
    [Param("center", "point", "centre of the circle", default=[0, 0, 0]),
     Param("radius", "length", "radius", required=True),
     Param("start_angle", "angle", "start angle, counter-clockwise from the plane's first axis", default=0),
     Param("end_angle", "angle", "end angle", required=True),
     Param("segments", "integer", "number of segments", default=12),
     Param("close", "enum", "none (open curve), chord (straight closing edge) or pie (through the centre)",
           default="none", choices=("none", "chord", "pie")),
     PLANE, NORMAL, X_AXIS],
    example={"op": "arc", "id": "arch", "center": [0, 0, 0], "radius": 1000, "start_angle": 0, "end_angle": 180,
             "plane": "xz", "close": "chord"})
def _arc(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    r = draw.arc(ctx.entities, ctx.pt(a["center"]), a["radius"], a["start_angle"], a["end_angle"],
                 a["segments"], _axes(ctx, a), close=a["close"])
    return _drawn(r)


@op("polygon", "draw", "Draw a regular polygon face.",
    [Param("center", "point", "centre point", default=[0, 0, 0]),
     Param("radius", "length", "distance from centre to the corners (or edge midpoints)", required=True),
     Param("sides", "integer", "number of sides (3 or more)", required=True),
     Param("inscribed", "bool", "corners on the radius (true) or edge midpoints on it (false)", default=True),
     PLANE, NORMAL],
    example={"op": "polygon", "id": "hex", "center": [0, 0, 0], "radius": 300, "sides": 6})
def _polygon(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    r = draw.polygon(ctx.entities, ctx.pt(a["center"]), a["radius"], a["sides"], _axes(ctx, a), a["inscribed"])
    return _drawn(r)


@op("face", "draw", "Draw a face from an outline of points (any flat polygon, optionally with holes).",
    [Param("points", "points", "outline points in order", required=True),
     Param("holes", "loops", "outlines of holes inside the face"),
     NORMAL],
    example={"op": "face", "id": "gable", "points": [[0, 0, 2400], [4000, 0, 2400], [2000, 0, 3400]]})
def _face(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    holes = [[ctx.pt(p) for p in loop] for loop in (a.get("holes") or [])]
    normal = ctx.vec(a["normal"]) if a.get("normal") is not None else None
    return _drawn(draw.face(ctx.entities, [ctx.pt(p) for p in a["points"]], holes, normal))


# ====================================================================== modify


@op("push_pull", "modify", "Extrude a face along its normal (SketchUp's Push/Pull).",
    [TARGET, FACE,
     Param("distance", "length", "how far to push (negative pushes into the solid)", required=True),
     Param("create_new", "bool", "keep the original face in place (like holding Ctrl)", default=False)],
    grows_target=True,
    notes="A lone face becomes a closed solid. Pushing a face of a solid adds material or cuts a pocket; "
          "pushing all the way through the opposite face cuts a hole. New faces join the target's reference.",
    example={"op": "push_pull", "target": "floor", "face": "top", "distance": "$h"})
def _push_pull(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    parts = []
    for part in a["target"].parts:
        created: list[Entity] = []
        for p in _picks(part, a.get("face")):
            assert p.face.parent is not None
            d = _local_length(p, a["distance"]) * (-1.0 if p.flip else 1.0)
            created += push_pull(p.face.parent, p.face, d, create_new=a["create_new"]).faces
        parts.append(created)
    return OpOutput(parts=parts)


@op("follow_me", "modify", "Sweep a profile face along a path (SketchUp's Follow Me).",
    [TARGET, FACE,
     Param("path", "path", "points of the path, or the id of earlier lines/arcs", required=True),
     Param("closed", "bool", "treat the path as a closed loop (default: closed if it ends where it starts)")],
    grows_target=True,
    notes="Place the profile at the start of the path, perpendicular to it. A closed circular path around "
          "an axis makes a lathe (vases, domes, rings).",
    example={"op": "follow_me", "target": "profile", "path": [[0, 0, 0], [2000, 0, 0], [2000, 2000, 0]]})
def _follow_me(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    path = a["path"]
    if isinstance(path, Target):
        edges = [e for e in path.all() if isinstance(e, Edge)]
        if not edges:
            raise GeometryError(f"'{path.name}' contains no edges to follow")
        world = _world_of(edges[0])
        pts_world = [apply_point(world, p) for p in path_from_edges(edges)]
    else:
        pts_world = [ctx.world_point(p) for p in path]
    parts = []
    for part in a["target"].parts:
        created: list[Entity] = []
        for p in _picks(part, a.get("face")):
            assert p.face.parent is not None
            local = [apply_point(inverse(p.world), q) for q in pts_world]
            created += follow_me(p.face.parent, p.face, local, a.get("closed"))
        parts.append(created)
    return OpOutput(parts=parts)


@op("offset", "modify", "Draw a copy of a face's outline inside (positive) or outside (negative) it.",
    [TARGET, FACE, Param("distance", "length", "offset distance; positive is inward", required=True)],
    grows_target=True,
    notes="The new outline splits the face, so the inner face can then be pushed or pulled "
          "(e.g. hollow out a box, or make a picture frame).",
    example={"op": "offset", "target": "box", "face": "top", "distance": 50})
def _offset(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    parts = []
    for part in a["target"].parts:
        created: list[Entity] = []
        for p in _picks(part, a.get("face")):
            assert p.face.parent is not None
            created += offset_face(p.face.parent, p.face, _local_length(p, a["distance"]))
        parts.append(created)
    return OpOutput(parts=parts)


@op("extrude", "modify", "Make a prism: a face from an outline, pushed along a direction.",
    [Param("points", "points", "outline of the profile", required=True),
     Param("height", "length", "distance along the profile's normal (right-hand rule on the points)"),
     Param("direction", "vector", "explicit extrusion vector (instead of height)"),
     GROUP, NAME],
    one_of=[("height", "direction")],
    example={"op": "extrude", "id": "roof", "points": [[0, 0, 2400], [4000, 0, 2400], [2000, 0, 3400]],
             "direction": [0, 3000, 0]})
def _extrude(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    pts = a["points"]

    def build(ents: Entities) -> None:
        if a.get("direction") is not None:
            vector = a["direction"]
        elif a.get("height") is not None:
            from pymodeler.core.vec import Plane

            vector = Plane.from_points(pts).normal * a["height"]
        else:
            raise GeometryError("extrude needs 'height' or 'direction'")
        extrude(ents, pts, vector)

    return OpOutput.single(_grouped(ctx, a, "Extrusion", build))


@op("opening", "modify", "Cut a rectangular opening (door, window) into a face of a solid or group.",
    [TARGET, Param("face", "face", "the face to cut into, e.g. \"front\"", required=True),
     Param("origin", "point", "lower-left corner of the opening, on (or near) the face", required=True),
     Param("width", "length", "horizontal size along the face", required=True),
     Param("height", "length", "vertical size along the face", required=True),
     Param("depth", "depth", "how deep to cut, or \"through\" to cut through the wall", default="through")],
    notes="Width runs horizontally along the face (left to right as seen from outside) and height "
          "runs up it; on a horizontal face width runs along X and height along Y.",
    example={"op": "opening", "target": "walls", "face": "front", "origin": [1200, 0, 0],
             "width": 900, "height": 2100})
def _opening(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    origin_w = ctx.world_point(a["origin"])
    for part in a["target"].parts:
        picks = _picks(part, a["face"])
        p = _closest_pick(picks, origin_w, a["width"], a["height"])
        _cut_opening(p, origin_w, a["width"], a["height"], a["depth"])
    return OpOutput()


def _opening_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Horizontal and vertical in-face directions used by the opening op."""
    if abs(normal[2]) > 0.9:
        u = np.array([1.0, 0.0, 0.0])
        return u, normalize(np.cross(normal, u))
    u = normalize(np.cross(np.array([0.0, 0.0, 1.0]), normal))
    return u, normalize(np.cross(normal, u))


def _closest_pick(picks: list[Picked], origin: np.ndarray, width: float, height: float) -> Picked:
    """The picked face that contains the opening's centre (or the first one)."""
    for p in picks:
        u, v = _opening_axes(p.normal)
        centre = origin + u * width / 2 + v * height / 2
        local = apply_point(inverse(p.world), centre)
        if p.face.contains_point(p.face.plane.project(local), tol=1.0):
            return p
    return picks[0]


def _cut_opening(p: Picked, origin_w: np.ndarray, width: float, height: float, depth: Any) -> None:
    """Draw the opening rectangle on the face and push it in."""
    ents = p.face.parent
    assert ents is not None
    n = p.normal
    u, v = _opening_axes(n)
    plane_pt = p.centroid
    o = origin_w - n * float((origin_w - plane_pt) @ n)
    corners_w = [o, o + u * width, o + u * width + v * height, o + v * height]
    inv = inverse(p.world)
    corners = [apply_point(inv, c) for c in corners_w]
    local_normal = p.face.normal * (-1.0 if p.flip else 1.0)
    centre_local = apply_point(inv, o + u * width / 2 + v * height / 2)
    faces = ents.add_face(corners, normal=local_normal)
    inner = [f for f in faces if f.contains_point(centre_local, tol=1.0)]
    if not inner:
        raise GeometryError("the opening does not lie on the selected face")
    face = inner[0]
    if depth == "through":
        thickness = _thickness(ents, face, centre_local)
    else:
        thickness = float(depth) / max(float(np.linalg.norm(p.world[:3, :3] @ face.normal)), 1e-12)
    push_pull(ents, face, -thickness)


def _thickness(ents: Entities, face: Face, centre: np.ndarray) -> float:
    """Distance from a face to the nearest opposite face behind it."""
    n = face.normal
    best = None
    for other in ents.faces.values():
        if other is face or float(other.normal @ n) > -0.9998:
            continue
        d = float((centre - other.plane.project(centre)) @ n)
        if d <= 1e-6:
            continue
        hit = centre - n * d
        if other.contains_point(hit, tol=1.0) and (best is None or d < best):
            best = d
    if best is None:
        raise GeometryError("could not find the far side of the wall; give 'depth' as a number")
    return best


# ====================================================================== transform


@op("move", "transform", "Move the target by a vector, or from one point to another.",
    [TARGET, Param("face", "face", "move only these faces (connected geometry stretches)"),
     Param("by", "vector", "displacement [dx, dy, dz]"),
     Param("to", "point", "destination of the 'from' point"),
     Param("from", "point", "reference point (default: the target's bounding-box minimum)")],
    one_of=[("by", "to")],
    example={"op": "move", "target": "table", "by": [0, 500, 0]})
def _move(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = _items(a)
    if a.get("by") is not None:
        delta = ctx.world_vec(a["by"])
    else:
        if a.get("to") is None:
            raise GeometryError("move needs 'by', or 'to' (optionally with 'from')")
        start = ctx.world_point(a["from"]) if a.get("from") is not None else _anchor(ctx, "min", items)
        delta = ctx.world_point(a["to"]) - start
    _apply_world(items, translation(delta))
    return OpOutput(parts=[list(part) for part in a["target"].parts])


@op("rotate", "transform", "Rotate the target about an axis.",
    [TARGET, Param("face", "face", "rotate only these faces"),
     Param("angle", "angle", "degrees, counter-clockwise looking down the axis", required=True),
     Param("axis", "direction", "rotation axis", default="z"),
     Param("center", "anchor", "pivot point", default="center")],
    example={"op": "rotate", "target": "chair", "angle": 90, "axis": "z", "center": "center"})
def _rotate(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = _items(a)
    m = rotation(ctx.world_vec(a["axis"]), a["angle"], _anchor(ctx, a["center"], items))
    _apply_world(items, m)
    return OpOutput(parts=[list(part) for part in a["target"].parts])


@op("scale", "transform", "Scale the target uniformly or per axis.",
    [TARGET, Param("face", "face", "scale only these faces (e.g. the top of a box to taper it)"),
     Param("factor", "scale", "scale factor, or [sx, sy, sz]", required=True),
     Param("center", "anchor", "fixed point", default="center")],
    notes="Scaling a single face (e.g. the top of a box) about its centre makes tapers and pyramids.",
    example={"op": "scale", "target": "box", "face": "top", "factor": 0.5})
def _scale(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = _items(a)
    _apply_world(items, scaling(a["factor"], _anchor(ctx, a["center"], items)))
    return OpOutput(parts=[list(part) for part in a["target"].parts])


@op("mirror", "transform", "Mirror the target across a plane (optionally keeping the original).",
    [TARGET,
     Param("axis", "direction", "normal of the mirror plane (\"x\" flips left/right)", default="x"),
     Param("center", "anchor", "a point on the mirror plane", default="center"),
     Param("copy", "bool", "keep the original and add a mirrored copy", default=False)],
    example={"op": "mirror", "target": "left_wing", "axis": "x", "center": [0, 0, 0], "copy": True})
def _mirror(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = a["target"].all()
    n = normalize(ctx.world_vec(a["axis"]))
    c = _anchor(ctx, a["center"], items)
    reflect = np.eye(4)
    reflect[:3, :3] -= 2.0 * np.outer(n, n)
    m = translation(c) @ reflect @ translation(-c)
    if a["copy"]:
        return OpOutput.single(_copy_world(ctx, items, m))
    _apply_world(items, m)
    return OpOutput(parts=[list(part) for part in a["target"].parts])


@op("copy", "transform", "Copy the target, displaced by a vector.",
    [TARGET,
     Param("by", "vector", "displacement of the copy"),
     Param("to", "point", "where the 'from' point of the copy goes"),
     Param("from", "point", "reference point (default: the target's bounding-box minimum)")],
    one_of=[("by", "to")],
    example={"op": "copy", "id": "chair2", "target": "chair", "by": [800, 0, 0]})
def _copy(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = a["target"].all()
    if a.get("by") is not None:
        delta = ctx.world_vec(a["by"])
    else:
        if a.get("to") is None:
            raise GeometryError("copy needs 'by', or 'to' (optionally with 'from')")
        start = ctx.world_point(a["from"]) if a.get("from") is not None else _anchor(ctx, "min", items)
        delta = ctx.world_point(a["to"]) - start
    return OpOutput.single(_copy_world(ctx, items, translation(delta)))


@op("array", "transform", "Make several copies in a row (offset) or around an axis (angle).",
    [TARGET,
     Param("count", "integer", "number of copies to add (the original stays)", required=True),
     Param("offset", "vector", "displacement between neighbours (linear array)"),
     Param("angle", "angle", "angle between neighbours (polar array)"),
     Param("axis", "direction", "polar array axis", default="z"),
     Param("center", "anchor", "polar array centre", default=[0, 0, 0])],
    one_of=[("offset", "angle")],
    notes="Each copy becomes one part of the step's id, so 'posts[2]' is the third copy.",
    example={"op": "array", "id": "posts", "target": "post", "count": 4, "offset": [1000, 0, 0]})
def _array(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    items = a["target"].all()
    if a["count"] < 1:
        raise GeometryError("array count must be at least 1")
    parts = []
    if a.get("offset") is not None:
        step = ctx.world_vec(a["offset"])
        for k in range(1, a["count"] + 1):
            parts.append(_copy_world(ctx, items, translation(step * k)))
    elif a.get("angle") is not None:
        axis = ctx.world_vec(a["axis"])
        centre = _anchor(ctx, a["center"], items)
        for k in range(1, a["count"] + 1):
            parts.append(_copy_world(ctx, items, rotation(axis, a["angle"] * k, centre)))
    else:
        raise GeometryError("array needs 'offset' (linear) or 'angle' (polar)")
    return OpOutput(parts=parts)


# ====================================================================== organise


@op("group", "organize", "Build geometry inside a new group from nested steps.",
    [NAME, Param("steps", "steps", "steps to run inside the group", required=True)],
    notes="Geometry in a group never sticks to anything outside it. Nested steps use the same "
          "coordinates as the parent, and their ids stay usable afterwards.",
    example={"op": "group", "id": "table", "name": "Table", "steps": [
        {"op": "box", "id": "top", "origin": [0, 0, 720], "size": [1600, 900, 30]}]})
def _group(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    if ctx.engine is None:
        raise GeometryError("nested steps need the build-script engine")
    name = a.get("name") or ""
    definition = ctx.model.add_definition(name or "Group", is_group=True)
    inst = add_instance(ctx.entities, definition, ctx.to_active)
    inst.name = name
    ctx.engine.run_nested(a["steps"], definition.entities, ctx.frame)
    return OpOutput(parts=[[inst]], names={name: [[inst]]} if name else {})


@op("make_group", "organize", "Turn existing geometry into a group.",
    [TARGET, NAME],
    notes="References to the grouped geometry now point at the group.",
    example={"op": "make_group", "target": "floor", "name": "Room"})
def _make_group(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    groups = []
    for ents, items in _by_context(a["target"].all()).values():
        groups.append(make_group(ctx.model, ents, items, name=a.get("name") or ""))
    name = a.get("name")
    return OpOutput(parts=[list(groups)], names={name: [list(groups)]} if name else {})


@op("component", "organize", "Define a reusable component from nested steps (place it with 'place').",
    [NAME, Param("steps", "steps", "steps that build the component around its own origin", required=True),
     Param("description", "string", "description of the component")],
    notes="The component is not placed by this step. Its geometry is built around [0, 0, 0], which "
          "becomes the insertion point used by 'place'.",
    example={"op": "component", "id": "post", "steps": [
        {"op": "cylinder", "center": [0, 0, 0], "radius": 50, "height": 1000, "group": False}]})
def _component(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    if ctx.engine is None:
        raise GeometryError("nested steps need the build-script engine")
    name = a.get("name") or ctx.engine.current_id or "Component"
    definition = ctx.model.add_definition(name, is_group=False)
    definition.description = a.get("description") or ""
    ctx.engine.register_component(ctx.engine.current_id or name, definition)
    ctx.engine.run_nested(a["steps"], definition.entities, np.eye(4))
    return OpOutput()


@op("make_component", "organize", "Turn existing geometry into a component instance.",
    [TARGET, NAME, Param("origin", "point", "insertion point (default: bounding-box minimum)")],
    example={"op": "make_component", "target": "leg", "name": "Leg"})
def _make_component(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    made = []
    for ents, items in _by_context(a["target"].all()).values():
        origin = None
        if a.get("origin") is not None:
            origin = apply_point(inverse(context_world(ents)), ctx.world_point(a["origin"]))
        inst = make_group(ctx.model, ents, items, name=a.get("name") or "", component=True, origin=origin)
        if ctx.engine is not None:
            ctx.engine.register_component(ctx.engine.current_id or inst.definition.name, inst.definition)
        made.append(inst)
    return OpOutput(parts=[list(made)])


@op("place", "organize", "Place an instance of a component.",
    [Param("component", "component", "id of the component to place", required=True),
     Param("position", "point", "where the component's origin goes", default=[0, 0, 0]),
     Param("rotation", "rotation", "rotation in degrees about Z, or {\"axis\": ..., \"angle\": ...}", default=0),
     Param("scale", "scale", "scale factor or [sx, sy, sz]", default=1),
     NAME],
    example={"op": "place", "component": "post", "position": [0, 0, 0],
             "repeat": {"count": 5, "offset": [1000, 0, 0]}})
def _place(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    definition: ComponentDefinition = a["component"]
    axis, angle = a["rotation"]
    local = translation(a["position"]) @ rotation(axis, angle) @ scaling(a["scale"])
    inst = place_component(ctx.entities, definition, ctx.to_active @ local, a.get("name") or "")
    return OpOutput.single([inst])


@op("explode", "organize", "Explode groups or component instances back into raw geometry.",
    [TARGET],
    example={"op": "explode", "target": "table"})
def _explode(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    parts = []
    for part in a["target"].parts:
        out: list[Entity] = []
        for inst in [e for e in part if isinstance(e, ComponentInstance) and e.parent is not None]:
            out += explode(ctx.model, inst.parent, inst)  # type: ignore[arg-type]
        parts.append(out)
    return OpOutput(parts=parts)


@op("paint", "organize", "Apply a material to faces, groups or components.",
    [TARGET,
     Param("material", "material", "material name (null resets to the default)", required=True),
     Param("face", "face", "only paint these faces of the target"),
     Param("side", "enum", "which side of faces to paint", default="both", choices=("front", "back", "both"))],
    notes="Painting a group or component colours all of its unpainted faces.",
    example={"op": "paint", "target": "Room", "material": "brick"})
def _paint(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    side = a["side"]
    for part in a["target"].parts:
        if a.get("face") is None:
            paint(ctx.model, part, a["material"], side)
            continue
        for p in _picks(part, a["face"]):
            s = {"front": "back", "back": "front"}.get(side, side) if p.flip else side
            paint(ctx.model, [p.face], a["material"], s)
    return OpOutput()


@op("set_tag", "organize", "Put entities on a tag (layer); the tag is created if needed.",
    [TARGET, Param("tag", "string", "tag name", required=True)],
    example={"op": "set_tag", "target": "roof", "tag": "Roof"})
def _set_tag(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    set_tag(ctx.model, a["target"].all(), a["tag"])
    return OpOutput()


@op("hide", "organize", "Hide (or show) entities.",
    [TARGET, Param("hidden", "bool", "true hides, false shows", default=True)],
    example={"op": "hide", "target": "helper_lines"})
def _hide(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    set_hidden(a["target"].all(), a["hidden"])
    return OpOutput()


@op("erase", "edit", "Erase entities (or just some faces of the target).",
    [TARGET, Param("face", "face", "only erase these faces of the target")],
    aliases=("delete",),
    notes="With 'face', only those faces go and their edges stay (as in SketchUp). Without it the whole "
          "target goes, including edges left bounding nothing.",
    example={"op": "erase", "target": "construction_lines"})
def _erase(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    for part in a["target"].parts:
        if a.get("face") is not None:
            # Like SketchUp: deleting faces keeps their edges.
            for ents, items in _by_context([p.face for p in _picks(part, a["face"])]).values():
                ents.erase_faces([f for f in items if isinstance(f, Face)])
            continue
        for ents, items in _by_context(list(part)).values():
            edges = {e for f in items if isinstance(f, Face) for e in f.edges()}
            ents.erase(items)
            ents.remove_stray_edges([e for e in edges if e.alive])
    return OpOutput()


@op("intersect", "edit", "Add edges where faces of the target cross other faces (Intersect Faces).",
    [TARGET, Param("with", "target", "what to intersect with (default: everything else in the model)")],
    example={"op": "intersect", "target": "roof", "with": "walls"})
def _intersect(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    mine = [WorldFace(p.face, p.world) for p in candidate_faces(a["target"].all())]
    if a.get("with") is not None:
        others = [WorldFace(p.face, p.world) for p in candidate_faces(a["with"].all())]
    else:
        own = {id(w.face) for w in mine}
        others = []
        for placement in ctx.model.iter_placements():
            for f in placement.entities.faces.values():
                if id(f) not in own:
                    others.append(WorldFace(f, placement.transform))
    return OpOutput.single(list(intersect_faces(mine, others)))


@op("set", "organize", "Set (or change) variables for the following steps.",
    [Param("variables", "variables", "names and values; expressions may use geometry queries", required=True)],
    example={"op": "set", "variables": {"top": "@table.zmax"}})
def _set(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    if ctx.engine is None:
        raise GeometryError("'set' needs the build-script engine")
    ctx.engine.set_variables(a["variables"])
    return OpOutput()


# ====================================================================== primitives


@op("box", "primitive", "An axis-aligned box (in its own group by default).",
    [Param("origin", "point", "minimum corner (or base centre when center is true)", default=[0, 0, 0]),
     Param("size", "size", "[width, depth, height]"),
     Param("width", "length", "size along X"), Param("depth", "length", "size along Y"),
     Param("height", "length", "size along Z"),
     Param("center", "bool", "centre the box on origin in X and Y", default=False),
     GROUP, NAME],
    one_of=[("size", "width")],
    example={"op": "box", "id": "top", "origin": [0, 0, 720], "size": [1600, 900, 30], "material": "oak"})
def _box(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    if a.get("size") is not None:
        size = a["size"]
    else:
        missing = [k for k in ("width", "depth", "height") if a.get(k) is None]
        if missing:
            raise GeometryError(f"box needs 'size' or all of width/depth/height (missing {', '.join(missing)})")
        size = np.array([a["width"], a["depth"], a["height"]])
    return OpOutput.single(_grouped(ctx, a, "Box", lambda e: primitives.box(e, a["origin"], size, a["center"])))


@op("cylinder", "primitive", "A cylinder standing on its base centre (in its own group by default).",
    [Param("center", "point", "centre of the base", default=[0, 0, 0]),
     Param("radius", "length", "radius", required=True),
     Param("height", "length", "height along the axis", required=True),
     SEGMENTS, Param("axis", "direction", "direction from base to top", default="z"), GROUP, NAME],
    example={"op": "cylinder", "id": "tower", "center": [0, 0, 0], "radius": 2000, "height": 8000, "segments": 48})
def _cylinder(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    def build(e: Entities) -> None:
        primitives.cylinder(e, a["center"], a["radius"], a["height"], a["segments"], a["axis"])

    return OpOutput.single(_grouped(ctx, a, "Cylinder", build))


@op("cone", "primitive", "A cone, frustum (top_radius > 0) or pyramid (segments 4) on its base centre.",
    [Param("center", "point", "centre of the base", default=[0, 0, 0]),
     Param("radius", "length", "base radius", required=True),
     Param("height", "length", "height along the axis", required=True),
     Param("top_radius", "length", "radius of a flat top (0 makes a point)", default=0),
     SEGMENTS, Param("axis", "direction", "direction from base to tip", default="z"), GROUP, NAME],
    example={"op": "cone", "id": "roof", "center": [0, 0, 8000], "radius": 2400, "height": 3000})
def _cone(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    def build(e: Entities) -> None:
        primitives.cone(e, a["center"], a["radius"], a["height"], a["segments"], a["top_radius"], a["axis"])

    return OpOutput.single(_grouped(ctx, a, "Cone", build))


@op("sphere", "primitive", "A sphere (in its own group by default).",
    [Param("center", "point", "centre", default=[0, 0, 0]),
     Param("radius", "length", "radius", required=True),
     SEGMENTS, Param("rings", "integer", "segments from pole to pole", default=12), GROUP, NAME],
    example={"op": "sphere", "id": "ball", "center": [0, 0, 500], "radius": 500})
def _sphere(ctx: OpContext, a: dict[str, Any]) -> OpOutput:
    def build(e: Entities) -> None:
        primitives.sphere(e, a["center"], a["radius"], a["segments"], a["rings"])

    return OpOutput.single(_grouped(ctx, a, "Sphere", build))

