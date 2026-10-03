"""Triangle meshes and edge lines extracted from a model in world coordinates.

Exporters and both renderers use this module so they agree on what the model looks
like: faces are triangulated, placed through the instance tree, coloured with their
effective material (a group's material shows on its unpainted faces), and smoothed
across soft/smooth edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.entities import Face, Vertex
from pymodeler.core.materials import DEFAULT_BACK_COLOR, DEFAULT_FRONT_COLOR, Color
from pymodeler.core.model import Model
from pymodeler.core.transform import apply_points, is_mirroring

DEFAULT_MATERIAL = "Default"
"""Key used for faces that have no material."""


@dataclass
class MeshPart:
    """All triangles sharing one material."""

    material: str
    color: Color
    opacity: float
    positions: list[np.ndarray] = field(default_factory=list)
    normals: list[np.ndarray] = field(default_factory=list)

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """``(positions, normals)`` as (3N, 3) arrays, three rows per triangle."""
        if not self.positions:
            return np.zeros((0, 3)), np.zeros((0, 3))
        return np.vstack(self.positions), np.vstack(self.normals)

    @property
    def triangle_count(self) -> int:
        """Number of triangles in the part."""
        return sum(len(p) for p in self.positions) // 3


@dataclass
class ModelMesh:
    """Front faces, back faces and visible edges of a model."""

    parts: dict[str, MeshPart] = field(default_factory=dict)
    back_parts: dict[str, MeshPart] = field(default_factory=dict)
    edges: list[np.ndarray] = field(default_factory=list)
    """Line segments as (2, 3) arrays."""
    edge_faded: list[bool] = field(default_factory=list)
    """Per edge: drawn faded because it lies outside the group being edited."""

    def edge_array(self) -> np.ndarray:
        """All edges as an (M, 2, 3) array."""
        return np.array(self.edges).reshape(-1, 2, 3) if self.edges else np.zeros((0, 2, 3))

    @property
    def triangle_count(self) -> int:
        """Number of front-side triangles."""
        return sum(p.triangle_count for p in self.parts.values())

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        """World bounding box of the triangles and edges."""
        chunks = [p.arrays()[0] for p in self.parts.values() if p.positions]
        if self.edges:
            chunks.append(self.edge_array().reshape(-1, 3))
        if not chunks:
            return None
        pts = np.vstack(chunks)
        return pts.min(axis=0), pts.max(axis=0)


def build_mesh(
    model: Model, include_back: bool = False, visible_only: bool = True, focus: tuple | None = None
) -> ModelMesh:
    """Triangulate every visible face of the model in world space.

    Args:
        include_back: Also produce back-side triangles (reversed) coloured with the
            faces' back materials, as the viewport shows them.
        visible_only: Skip hidden entities and entities on hidden tags.
        focus: Instance path of the group being edited; everything outside it is faded.
    """
    mesh = ModelMesh()
    for placement in model.iter_placements(visible_only):
        faded = bool(focus) and placement.path[: len(focus)] != tuple(focus)  # type: ignore[arg-type]
        world = placement.transform
        mirrored = is_mirroring(world)
        normal_matrix = np.linalg.inv(world[:3, :3]).T
        ents = placement.entities
        for face in ents.faces.values():
            if visible_only and (face.hidden or not model.is_tag_visible(face.tag)):
                continue
            _add_face(mesh, model, face, world, normal_matrix, mirrored, placement.material, include_back, faded)
        for edge in ents.edges.values():
            if edge.soft or edge.hidden:
                continue
            if visible_only and not model.is_tag_visible(edge.tag):
                continue
            mesh.edges.append(apply_points(world, [edge.v1._t, edge.v2._t]))
            mesh.edge_faded.append(faded)
    return mesh


def _add_face(
    mesh: ModelMesh,
    model: Model,
    face: Face,
    world: np.ndarray,
    normal_matrix: np.ndarray,
    mirrored: bool,
    inherited: str | None,
    include_back: bool,
    faded: bool = False,
) -> None:
    """Append one face's triangles (and optionally its back side) to the mesh."""
    try:
        points, tris = face.triangles()
    except ValueError:
        return
    if not tris:
        return
    verts = [v for loop in face.loops for v in loop]
    normals = np.array([_vertex_normal(face, v) for v in verts])
    world_pts = apply_points(world, points)
    world_n = normals @ normal_matrix.T
    world_n /= np.maximum(np.linalg.norm(world_n, axis=1, keepdims=True), 1e-12)
    idx = np.array(tris, dtype=np.int64)
    if mirrored:
        idx = idx[:, ::-1]
    front = _part(mesh.parts, model, face.material or inherited, DEFAULT_FRONT_COLOR, faded)
    front.positions.append(world_pts[idx].reshape(-1, 3))
    front.normals.append(world_n[idx].reshape(-1, 3))
    if include_back:
        back = _part(mesh.back_parts, model, face.back_material or inherited, DEFAULT_BACK_COLOR, faded)
        back.positions.append(world_pts[idx[:, ::-1]].reshape(-1, 3))
        back.normals.append(-world_n[idx[:, ::-1]].reshape(-1, 3))


FADE_COLOR = (0.86, 0.86, 0.86)
FADE_AMOUNT = 0.6


def _part(
    parts: dict[str, MeshPart], model: Model, material: str | None, default: Color, faded: bool = False
) -> MeshPart:
    """The mesh part for a material (and fade state), created on first use."""
    name = material if material in model.materials else DEFAULT_MATERIAL
    key = name + ("|faded" if faded else "")
    if key not in parts:
        if name == DEFAULT_MATERIAL:
            color, opacity = default, 1.0
        else:
            color, opacity = model.materials[name].color, model.materials[name].opacity
        if faded:
            color = tuple(c * (1 - FADE_AMOUNT) + f * FADE_AMOUNT for c, f in zip(color, FADE_COLOR, strict=True))  # type: ignore[assignment]
        parts[key] = MeshPart(name, color, opacity)
    return parts[key]


def _vertex_normal(face: Face, vertex: Vertex) -> np.ndarray:
    """Normal at ``vertex`` for ``face``, averaged across smooth edges around it."""
    group = {face}
    frontier = [face]
    while frontier:
        current = frontier.pop()
        for e in vertex.edges:
            if not e.smooth or current not in e.faces:
                continue
            for other in e.faces:
                if other not in group and float(other.normal @ face.normal) > 0.0:
                    group.add(other)
                    frontier.append(other)
    if len(group) == 1:
        return face.normal
    total = sum((f.normal for f in group), np.zeros(3))
    length = float(np.linalg.norm(total))
    return total / length if length > 1e-12 else face.normal
