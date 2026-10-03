"""Find what is under the cursor: vertices, edges and faces (no Qt dependency).

A :class:`PickScene` flattens the model's visible geometry (through groups and
components) into numpy arrays once per model change, so every mouse move can test
thousands of entities with a few vectorised operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.entities import Edge, Entities, Face, Vertex
from pymodeler.core.model import Model
from pymodeler.core.transform import apply_normal, apply_points
from pymodeler.render.camera import Camera

POINT_TOLERANCE = 12.0
"""Pixels within which a point (endpoint, midpoint) snaps."""
EDGE_TOLERANCE = 7.0
"""Pixels within which an edge is picked."""


@dataclass
class Hit:
    """Something found under the cursor."""

    kind: str
    """``"vertex"``, ``"midpoint"``, ``"edge"`` or ``"face"``."""
    entity: Vertex | Edge | Face
    point: np.ndarray
    """World position of the hit."""
    depth: float
    """Distance from the eye along the view direction."""
    screen_distance: float = 0.0
    world: np.ndarray = field(default_factory=lambda: np.eye(4))
    """Transform from the entity's collection to world space."""
    path: tuple = ()
    """Instances (outermost first) containing the entity; empty for root geometry."""

    @property
    def entities(self) -> Entities | None:
        """The collection holding the entity."""
        return self.entity.parent


@dataclass
class _Projected:
    xy: np.ndarray
    depth: np.ndarray
    visible: np.ndarray


class PickScene:
    """World-space geometry of a model, prepared for picking."""

    def __init__(self, model: Model) -> None:
        self.model = model
        verts, vert_refs = [], []
        edges, edge_refs = [], []
        tris, tri_refs = [], []
        self.face_normals: dict[int, np.ndarray] = {}
        for placement in model.iter_placements():
            world = placement.transform
            ents = placement.entities
            meta = (world, placement.path)
            for v in ents.vertices.values():
                if any(not e.hidden and model.is_tag_visible(e.tag) for e in v.edges) or not v.edges:
                    verts.append(v._t)
                    vert_refs.append((v, *meta))
            for e in ents.edges.values():
                if e.hidden or not model.is_tag_visible(e.tag):
                    continue
                edges.append((e.v1._t, e.v2._t))
                edge_refs.append((e, *meta))
            for f in ents.faces.values():
                if f.hidden or not model.is_tag_visible(f.tag):
                    continue
                try:
                    points, triangles = f.triangles()
                except ValueError:
                    continue
                wp = apply_points(world, points)
                for a, b, c in triangles:
                    tris.append((wp[a], wp[b], wp[c]))
                    tri_refs.append((f, *meta))
                self.face_normals[id(f)] = apply_normal(world, f.normal)
        self.vertices = self._world(verts, vert_refs)
        self.vertex_refs = vert_refs
        self.edges = self._world_edges(edges, edge_refs)
        self.edge_refs = edge_refs
        self.triangles = np.array(tris, dtype=float).reshape(-1, 3, 3)
        self.triangle_refs = tri_refs

    @staticmethod
    def _world(points: list, refs: list) -> np.ndarray:
        out = np.zeros((len(points), 3))
        for i, (p, (_, world, _)) in enumerate(zip(points, refs, strict=True)):
            out[i] = world[:3, :3] @ np.asarray(p) + world[:3, 3]
        return out

    @staticmethod
    def _world_edges(edges: list, refs: list) -> np.ndarray:
        out = np.zeros((len(edges), 2, 3))
        for i, ((a, b), (_, world, _)) in enumerate(zip(edges, refs, strict=True)):
            out[i, 0] = world[:3, :3] @ np.asarray(a) + world[:3, 3]
            out[i, 1] = world[:3, :3] @ np.asarray(b) + world[:3, 3]
        return out

    # ------------------------------------------------------------------ queries
    def ray_faces(self, origin: np.ndarray, direction: np.ndarray) -> list[Hit]:
        """Faces hit by a ray, nearest first (Möller-Trumbore, vectorised)."""
        if not len(self.triangles):
            return []
        v0, v1, v2 = self.triangles[:, 0], self.triangles[:, 1], self.triangles[:, 2]
        e1, e2 = v1 - v0, v2 - v0
        p = np.cross(direction, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        t_vec = origin - v0
        u = np.einsum("ij,ij->i", t_vec, p) * inv
        q = np.cross(t_vec, e1)
        v = (q @ direction) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)
        out: dict[int, Hit] = {}
        for i in np.flatnonzero(hit)[np.argsort(t[hit])]:
            face, world, path = self.triangle_refs[i]
            if id(face) in out:
                continue
            out[id(face)] = Hit("face", face, origin + direction * t[i], float(t[i]), 0.0, world, path)
        return sorted(out.values(), key=lambda h: h.depth)

    def project(self, camera: Camera, points: np.ndarray, width: int, height: int) -> _Projected:
        """Screen positions and view depths of world points."""
        near, far = camera.clip_range(1e5)
        mvp = camera.projection_matrix(width / max(height, 1), near, far) @ camera.view_matrix()
        hom = np.hstack([points.reshape(-1, 3), np.ones((points.reshape(-1, 3).shape[0], 1))]) @ mvp.T
        w = hom[:, 3]
        visible = w > 1e-9
        safe = np.where(visible, w, 1.0)
        xy = np.stack([(hom[:, 0] / safe + 1) * 0.5 * width, (1 - hom[:, 1] / safe) * 0.5 * height], axis=1)
        view = camera.view_matrix()
        depth = -(points.reshape(-1, 3) @ view[2, :3] + view[2, 3])
        return _Projected(xy, depth, visible)

    def near_points(
        self, camera: Camera, x: float, y: float, width: int, height: int, tolerance: float = POINT_TOLERANCE
    ) -> list[Hit]:
        """Vertices and edge midpoints within ``tolerance`` pixels, nearest first."""
        hits: list[Hit] = []
        cursor = np.array([x, y])
        if len(self.vertices):
            proj = self.project(camera, self.vertices, width, height)
            dist = np.linalg.norm(proj.xy - cursor, axis=1)
            for i in np.flatnonzero(proj.visible & (dist <= tolerance)):
                v, world, path = self.vertex_refs[i]
                hits.append(Hit("vertex", v, self.vertices[i].copy(), float(proj.depth[i]), float(dist[i]), world, path))
        if len(self.edges):
            mids = self.edges.mean(axis=1)
            proj = self.project(camera, mids, width, height)
            dist = np.linalg.norm(proj.xy - cursor, axis=1)
            for i in np.flatnonzero(proj.visible & (dist <= tolerance)):
                e, world, path = self.edge_refs[i]
                hits.append(Hit("midpoint", e, mids[i].copy(), float(proj.depth[i]), float(dist[i]), world, path))
        return sorted(hits, key=lambda h: (h.screen_distance, h.depth))

    def near_edges(
        self, camera: Camera, x: float, y: float, width: int, height: int, tolerance: float = EDGE_TOLERANCE
    ) -> list[Hit]:
        """Edges passing within ``tolerance`` pixels; ``point`` is the closest point on the edge."""
        if not len(self.edges):
            return []
        a = self.project(camera, self.edges[:, 0], width, height)
        b = self.project(camera, self.edges[:, 1], width, height)
        cursor = np.array([x, y])
        d = b.xy - a.xy
        length2 = np.einsum("ij,ij->i", d, d)
        t = np.clip(np.einsum("ij,ij->i", cursor - a.xy, d) / np.maximum(length2, 1e-12), 0.0, 1.0)
        closest = a.xy + d * t[:, None]
        dist = np.linalg.norm(closest - cursor, axis=1)
        mask = a.visible & b.visible & (dist <= tolerance)
        hits = []
        from pymodeler.ui.navigation import pixel_ray

        origin, direction = pixel_ray(camera, x, y, width, height)
        for i in np.flatnonzero(mask):
            e, world, path = self.edge_refs[i]
            p = closest_point_on_segment_to_ray(self.edges[i, 0], self.edges[i, 1], origin, direction)
            depth = float((p - camera.eye) @ -camera.basis()[2])
            hits.append(Hit("edge", e, p, depth, float(dist[i]), world, path))
        return sorted(hits, key=lambda h: (h.screen_distance, h.depth))


def closest_point_on_segment_to_ray(a: np.ndarray, b: np.ndarray, origin: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Point on segment ``ab`` closest to a ray."""
    u = b - a
    w = a - origin
    uu, ud, dd = float(u @ u), float(u @ direction), float(direction @ direction)
    uw, dw = float(u @ w), float(direction @ w)
    denom = uu * dd - ud * ud
    s = 0.0 if abs(denom) < 1e-12 or uu < 1e-12 else (ud * dw - dd * uw) / denom
    return a + u * min(1.0, max(0.0, s))


def closest_point_on_line_to_ray(
    point: np.ndarray, line_dir: np.ndarray, origin: np.ndarray, direction: np.ndarray
) -> np.ndarray:
    """Point on the infinite line ``point + t * line_dir`` closest to a ray."""
    w = point - origin
    a, b, c = float(line_dir @ line_dir), float(line_dir @ direction), float(direction @ direction)
    d, e = float(line_dir @ w), float(direction @ w)
    denom = a * c - b * b
    if abs(denom) < 1e-12:
        return point.copy()
    s = (b * e - c * d) / denom
    return point + line_dir * s
