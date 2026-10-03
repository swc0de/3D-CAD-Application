"""Flatten a model into renderer-friendly arrays (triangles, lines, helpers).

Both the software rasterizer and the OpenGL renderers consume :class:`SceneData`, so
the viewport, the GL previews and the software previews all show the same thing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pymodeler.core.model import Model
from pymodeler.io.mesh import build_mesh

EDGE_COLOR = (0.08, 0.08, 0.1)
FADED_EDGE_COLOR = (0.62, 0.62, 0.64)
CONTEXT_BOX_COLOR = (0.45, 0.45, 0.5)
AXIS_COLORS = ((0.85, 0.1, 0.1), (0.1, 0.6, 0.1), (0.1, 0.25, 0.9))
GRID_COLOR = (0.72, 0.74, 0.78)
GUIDE_COLOR = (0.3, 0.3, 0.38)
GUIDE_DASHES = 160


@dataclass
class SceneData:
    """Everything a renderer needs to draw a model."""

    positions: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    """Triangle corners, three rows per triangle (front and back sides)."""
    normals: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    colors: np.ndarray = field(default_factory=lambda: np.zeros((0, 4), np.float32))
    """RGBA per corner (alpha < 1 for transparent materials)."""
    lines: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    """Model edges, two rows per segment."""
    line_colors: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    helper_lines: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    """Axes and ground grid, two rows per segment."""
    helper_colors: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    bounds: tuple[np.ndarray, np.ndarray] | None = None
    grid_spacing: float = 0.0
    """Ground-grid spacing in millimetres (0 when there is no grid)."""

    @property
    def triangle_count(self) -> int:
        """Number of triangles (both sides)."""
        return len(self.positions) // 3

    @property
    def radius(self) -> float:
        """Half the diagonal of the scene bounds (at least 1 m)."""
        if self.bounds is None:
            return 1000.0
        return max(float(np.linalg.norm(self.bounds[1] - self.bounds[0])) / 2.0, 1000.0)


def build_scene(model: Model, axes: bool | str = True, grid: bool = True, focus: tuple | None = None,
                guides: bool = False) -> SceneData:
    """Collect the model's visible triangles and edges plus axes and a ground grid.

    ``axes`` is ``True``/``"short"`` (axes reaching just past the model, for previews),
    ``"long"`` (SketchUp-style axes through the origin, for the viewport) or ``False``.
    ``guides`` adds the model's construction guides as dashed lines.
    """
    mesh = build_mesh(model, include_back=True, focus=focus)
    pos, nrm, col = [], [], []
    for parts in (mesh.parts, mesh.back_parts):
        for part in parts.values():
            p, n = part.arrays()
            if not len(p):
                continue
            pos.append(p)
            nrm.append(n)
            col.append(np.tile([*part.color, part.opacity], (len(p), 1)))
    scene = SceneData()
    if pos:
        scene.positions = np.vstack(pos).astype(np.float32)
        scene.normals = np.vstack(nrm).astype(np.float32)
        scene.colors = np.vstack(col).astype(np.float32)
    edges = mesh.edge_array()
    if len(edges):
        scene.lines = edges.reshape(-1, 3).astype(np.float32)
        colors = [FADED_EDGE_COLOR if f else EDGE_COLOR for f in mesh.edge_faded for _ in (0, 1)]
        scene.line_colors = np.array(colors, dtype=np.float32).reshape(-1, 3)
    scene.bounds = mesh.bounds()
    helpers, helper_colors = [], []
    if grid:
        segs, spacing = ground_grid(scene.bounds)
        scene.grid_spacing = spacing
        if len(segs):
            helpers.append(segs)
            helper_colors.append(np.tile(GRID_COLOR, (len(segs), 1)))
    if axes:
        segs, colors = long_axis_lines() if axes == "long" else axis_lines(scene.bounds)
        helpers.append(segs)
        helper_colors.append(colors)
    if guides and model.guides:
        segs = guide_lines(model.guides, scene.bounds)
        helpers.append(segs)
        helper_colors.append(np.tile(GUIDE_COLOR, (len(segs), 1)))
    if focus:
        box = _context_box(focus)
        if len(box):
            helpers.append(box)
            helper_colors.append(np.tile(CONTEXT_BOX_COLOR, (len(box), 1)))
    if helpers:
        scene.helper_lines = np.vstack(helpers).astype(np.float32)
        scene.helper_colors = np.vstack(helper_colors).astype(np.float32)
    return scene


def guide_lines(guides: list, bounds: tuple[np.ndarray, np.ndarray] | None) -> np.ndarray:
    """Dashed segments for guide lines (clipped around the model) and crosses for guide points."""
    if bounds is None:
        centre, radius = np.zeros(3), 2000.0
    else:
        centre = (np.asarray(bounds[0]) + np.asarray(bounds[1])) / 2
        radius = max(float(np.linalg.norm(np.asarray(bounds[1]) - bounds[0])) / 2, 2000.0)
    segs: list[np.ndarray] = []
    reach = radius * 3.0
    step = 2 * reach / GUIDE_DASHES
    for guide in guides:
        if guide.direction is None:
            size = radius * 0.015
            for axis in np.eye(3):
                segs += [guide.point - axis * size, guide.point + axis * size]
            continue
        mid = guide.closest_point(centre)
        for k in range(GUIDE_DASHES):
            t = -reach + k * step
            segs += [mid + guide.direction * t, mid + guide.direction * (t + step * 0.55)]
    return np.array(segs, dtype=float).reshape(-1, 3)


def nice_step(span: float, target_lines: int = 12) -> float:
    """A 1/2/5 x 10^k spacing giving roughly ``target_lines`` divisions of ``span``."""
    raw = max(span, 1.0) / target_lines
    power = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        if raw <= mult * power:
            return mult * power
    return 10 * power


def ground_grid(bounds: tuple[np.ndarray, np.ndarray] | None) -> tuple[np.ndarray, float]:
    """Grid lines on z = 0 covering the model footprint (two rows per segment)."""
    if bounds is None:
        lo, hi = np.array([-2000.0, -2000.0, 0.0]), np.array([2000.0, 2000.0, 0.0])
    else:
        lo, hi = bounds
    span = max(float(hi[0] - lo[0]), float(hi[1] - lo[1]), 1000.0)
    step = nice_step(span)
    pad = step
    x0 = math.floor((min(lo[0], 0.0) - pad) / step) * step
    x1 = math.ceil((max(hi[0], 0.0) + pad) / step) * step
    y0 = math.floor((min(lo[1], 0.0) - pad) / step) * step
    y1 = math.ceil((max(hi[1], 0.0) + pad) / step) * step
    segs = []
    for x in np.arange(x0, x1 + step / 2, step):
        segs += [(x, y0, 0.0), (x, y1, 0.0)]
    for y in np.arange(y0, y1 + step / 2, step):
        segs += [(x0, y, 0.0), (x1, y, 0.0)]
    return np.array(segs, dtype=float), step


def axis_lines(bounds: tuple[np.ndarray, np.ndarray] | None) -> tuple[np.ndarray, np.ndarray]:
    """Red/green/blue axis segments from the origin, reaching just past the model."""
    lengths = np.full(3, 1000.0)
    if bounds is not None:
        size = float(np.max(bounds[1] - bounds[0]))
        lengths = np.maximum(np.asarray(bounds[1]) * 1.1, size * 0.2)
    segs, colors = [], []
    for axis in range(3):
        end = np.zeros(3)
        end[axis] = lengths[axis]
        segs += [np.zeros(3), end]
        colors += [AXIS_COLORS[axis]] * 2
    return np.array(segs), np.array(colors)


def long_axis_lines(length: float = 1.0e6) -> tuple[np.ndarray, np.ndarray]:
    """Axes through the origin: solid on the positive side, faded on the negative side."""
    segs, colors = [], []
    for axis in range(3):
        end = np.zeros(3)
        end[axis] = length
        segs += [np.zeros(3), end, np.zeros(3), -end]
        faded = tuple(0.55 + 0.45 * c for c in AXIS_COLORS[axis])
        colors += [AXIS_COLORS[axis]] * 2 + [faded] * 2
    return np.array(segs), np.array(colors)


def highlight_geometry(entities: list) -> tuple[np.ndarray, np.ndarray]:
    """World-space triangles and line segments outlining selected entities.

    Faces give their triangles plus outline; edges their segment; groups and components
    the twelve edges of their bounding box.

    Returns:
        ``(triangle_corners, line_points)`` as (3N, 3) and (2M, 3) float32 arrays.
    """
    from pymodeler.core.components import ComponentInstance, context_world, entities_bounds
    from pymodeler.core.entities import Edge, Face
    from pymodeler.core.transform import apply_points

    tris: list[np.ndarray] = []
    lines: list[np.ndarray] = []
    for e in entities:
        if e.parent is None:
            continue
        world = context_world(e.parent)
        if isinstance(e, Face):
            pts, triangles = e.triangles()
            wp = apply_points(world, pts)
            if triangles:
                tris.append(wp[np.array(triangles).reshape(-1)])
            for loop in e.loops:
                ring = apply_points(world, np.array([v._t for v in loop]))
                for i in range(len(ring)):
                    lines.append(np.array([ring[i], ring[(i + 1) % len(ring)]]))
        elif isinstance(e, Edge):
            lines.append(apply_points(world, np.array([e.v1._t, e.v2._t])))
        elif isinstance(e, ComponentInstance):
            box = entities_bounds(e.definition.entities, world @ e.transform)
            if box is None:
                continue
            lo, hi = box
            c = [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            for i, j in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
                lines.append(np.array([c[i], c[j]]))
    tri_arr = np.vstack(tris).astype(np.float32) if tris else np.zeros((0, 3), np.float32)
    line_arr = np.vstack(lines).astype(np.float32) if lines else np.zeros((0, 3), np.float32)
    return tri_arr, line_arr


def _context_box(focus: tuple) -> np.ndarray:
    """Wireframe box around the group being edited (world space)."""
    from pymodeler.core.components import entities_bounds
    from pymodeler.core.transform import identity

    world = identity()
    for inst in focus[:-1]:
        world = world @ inst.transform
    last = focus[-1]
    box = entities_bounds(last.definition.entities, world @ last.transform)
    if box is None:
        return np.zeros((0, 3))
    lo, hi = box
    pad = (hi - lo) * 0.02 + 5.0
    lo, hi = lo - pad, hi + pad
    c = [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    pairs = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
    return np.array([c[k] for pair in pairs for k in pair])
