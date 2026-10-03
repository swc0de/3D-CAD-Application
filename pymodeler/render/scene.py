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
AXIS_COLORS = ((0.85, 0.1, 0.1), (0.1, 0.6, 0.1), (0.1, 0.25, 0.9))
GRID_COLOR = (0.72, 0.74, 0.78)


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


def build_scene(model: Model, axes: bool | str = True, grid: bool = True) -> SceneData:
    """Collect the model's visible triangles and edges plus axes and a ground grid.

    ``axes`` is ``True``/``"short"`` (axes reaching just past the model, for previews),
    ``"long"`` (SketchUp-style axes through the origin, for the viewport) or ``False``.
    """
    mesh = build_mesh(model, include_back=True)
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
        scene.line_colors = np.tile(EDGE_COLOR, (len(scene.lines), 1)).astype(np.float32)
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
    if helpers:
        scene.helper_lines = np.vstack(helpers).astype(np.float32)
        scene.helper_colors = np.vstack(helper_colors).astype(np.float32)
    return scene


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
