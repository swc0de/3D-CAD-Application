"""A pure-numpy software rasterizer for headless previews.

It needs no GPU, no OpenGL and no display, so ``--preview`` works on any machine.
Triangles are depth-tested with Gouraud shading; edges are drawn on top with a small
depth bias; transparent materials are blended back to front.  Rendering at 2x and
downsampling gives anti-aliased output.
"""

from __future__ import annotations

import math

import numpy as np

from pymodeler.render.camera import Camera
from pymodeler.render.scene import SceneData

SKY_TOP = np.array([0.60, 0.74, 0.90], dtype=np.float32)
SKY_BOTTOM = np.array([0.93, 0.95, 0.97], dtype=np.float32)
LIGHT_DIR = np.array([-0.35, 0.55, 0.76])
AMBIENT = 0.42


def background(height: int, width: int) -> np.ndarray:
    """A vertical sky gradient image (float RGB)."""
    t = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    rows = SKY_TOP[None, :] * (1 - t) + SKY_BOTTOM[None, :] * t
    return np.repeat(rows[:, None, :], width, axis=1)


class _Raster:
    """Frame buffers plus the camera transform for one render."""

    def __init__(self, scene: SceneData, camera: Camera, width: int, height: int) -> None:
        self.w, self.h = width, height
        self.camera = camera
        near, far = camera.clip_range(scene.radius)
        self.near = near if camera.perspective else -math.inf
        self.view = camera.view_matrix()
        self.proj = camera.projection_matrix(width / height, near, far)
        self.color = background(height, width)
        self.depth = np.full((height, width), -np.inf, dtype=np.float64)
        self.bias_abs = scene.radius * 2e-3

    def to_camera(self, pts: np.ndarray) -> np.ndarray:
        """World points to camera space."""
        return pts @ self.view[:3, :3].T + self.view[:3, 3]

    def to_screen(self, cam: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Camera-space points to pixel x, pixel y and depth 'closeness' (bigger = nearer)."""
        hom = np.hstack([cam, np.ones((len(cam), 1))]) @ self.proj.T
        w = hom[:, 3:4]
        ndc = hom[:, :3] / np.where(np.abs(w) < 1e-12, 1e-12, w)
        sx = (ndc[:, 0] + 1.0) * 0.5 * self.w
        sy = (1.0 - ndc[:, 1]) * 0.5 * self.h
        dist = -cam[:, 2]
        q = 1.0 / np.maximum(dist, 1e-9) if self.camera.perspective else -dist
        return sx, sy, q

    def bias(self, q: np.ndarray) -> np.ndarray:
        """Depth tolerance that lets edges win over the faces they lie on."""
        if self.camera.perspective:
            return np.abs(q) * 4e-3
        return np.full_like(q, self.bias_abs)


def render_software(scene: SceneData, camera: Camera, width: int, height: int, supersample: int = 2) -> np.ndarray:
    """Render ``scene`` to an (height, width, 3) uint8 RGB image."""
    ss = max(1, int(supersample))
    r = _Raster(scene, camera, width * ss, height * ss)
    _draw_triangles(r, scene)
    line_px = max(1, ss)
    if len(scene.helper_lines):
        _draw_lines(r, scene.helper_lines, scene.helper_colors, line_px)
    if len(scene.lines):
        _draw_lines(r, scene.lines, scene.line_colors, line_px)
    img = r.color.reshape(height, ss, width, ss, 3).mean(axis=(1, 3))
    return (np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _draw_triangles(r: _Raster, scene: SceneData) -> None:
    """Rasterize all front-facing triangles: opaque first, then transparent back to front."""
    if not len(scene.positions):
        return
    cam = r.to_camera(scene.positions.astype(np.float64))
    sx, sy, q = r.to_screen(cam)
    normals = scene.normals.astype(np.float64) @ r.view[:3, :3].T
    light = LIGHT_DIR / np.linalg.norm(LIGHT_DIR)
    shade = AMBIENT + (1 - AMBIENT) * np.clip(normals @ light, 0.0, 1.0)
    rgb = scene.colors[:, :3].astype(np.float64) * shade[:, None]
    alpha = scene.colors[:, 3].astype(np.float64)
    n = len(sx) // 3
    tx, ty, tq = sx.reshape(n, 3), sy.reshape(n, 3), q.reshape(n, 3)
    trgb, ta = rgb.reshape(n, 3, 3), alpha.reshape(n, 3)[:, 0]
    in_front = np.all(-cam[:, 2].reshape(n, 3) > (r.near if r.camera.perspective else -np.inf), axis=1)
    area = (tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0]) - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0])
    visible = in_front & (area < -1e-9)
    opaque = np.flatnonzero(visible & (ta >= 0.999))
    clear = np.flatnonzero(visible & (ta < 0.999))
    for t in opaque:
        _fill(r, tx[t], ty[t], tq[t], trgb[t], area[t], None)
    for t in clear[np.argsort(tq[clear].mean(axis=1))]:
        _fill(r, tx[t], ty[t], tq[t], trgb[t], area[t], float(ta[t]))


def _fill(
    r: _Raster,
    xs: np.ndarray,
    ys: np.ndarray,
    qs: np.ndarray,
    rgb: np.ndarray,
    area: float,
    alpha: float | None,
) -> None:
    """Rasterize one triangle (blend instead of writing depth when ``alpha`` is set)."""
    x0, x1 = max(int(math.floor(xs.min())), 0), min(int(math.ceil(xs.max())), r.w - 1)
    y0, y1 = max(int(math.floor(ys.min())), 0), min(int(math.ceil(ys.max())), r.h - 1)
    if x0 > x1 or y0 > y1:
        return
    px = np.arange(x0, x1 + 1) + 0.5
    py = np.arange(y0, y1 + 1) + 0.5
    gx, gy = np.meshgrid(px, py)
    w0 = ((xs[1] - gx) * (ys[2] - gy) - (xs[2] - gx) * (ys[1] - gy)) / area
    w1 = ((xs[2] - gx) * (ys[0] - gy) - (xs[0] - gx) * (ys[2] - gy)) / area
    w2 = 1.0 - w0 - w1
    eps = -1e-6
    inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
    if not inside.any():
        return
    q = w0 * qs[0] + w1 * qs[1] + w2 * qs[2]
    zbuf = r.depth[y0:y1 + 1, x0:x1 + 1]
    mask = inside & (q > zbuf)
    if not mask.any():
        return
    col = w0[..., None] * rgb[0] + w1[..., None] * rgb[1] + w2[..., None] * rgb[2]
    target = r.color[y0:y1 + 1, x0:x1 + 1]
    if alpha is None:
        zbuf[mask] = q[mask]
        target[mask] = col[mask]
    else:
        target[mask] = col[mask] * alpha + target[mask] * (1.0 - alpha)


def _draw_lines(r: _Raster, points: np.ndarray, colors: np.ndarray, width: int) -> None:
    """Draw depth-tested line segments (two rows per segment) ``width`` pixels thick."""
    cam = r.to_camera(points.astype(np.float64)).reshape(-1, 2, 3)
    cols = colors.reshape(-1, 2, 3)[:, 0, :]
    xs_all, ys_all, qs_all, cs_all = [], [], [], []
    for seg, col in zip(cam, cols, strict=True):
        clipped = _clip_near(seg, r.near) if r.camera.perspective else seg
        if clipped is None:
            continue
        sx, sy, q = r.to_screen(clipped)
        steps = int(max(abs(sx[1] - sx[0]), abs(sy[1] - sy[0]))) + 2
        if steps > 20000:
            continue
        t = np.linspace(0.0, 1.0, steps)
        xs_all.append(sx[0] + (sx[1] - sx[0]) * t)
        ys_all.append(sy[0] + (sy[1] - sy[0]) * t)
        qs_all.append(q[0] + (q[1] - q[0]) * t)
        cs_all.append(np.repeat(col[None, :], steps, axis=0))
    if not xs_all:
        return
    xs, ys, qs, cs = np.concatenate(xs_all), np.concatenate(ys_all), np.concatenate(qs_all), np.vstack(cs_all)
    offsets = [(dx, dy) for dx in range(width) for dy in range(width)]
    tol = r.bias(qs)
    for dx, dy in offsets:
        ix = np.floor(xs).astype(np.int64) + dx - width // 2
        iy = np.floor(ys).astype(np.int64) + dy - width // 2
        ok = (ix >= 0) & (ix < r.w) & (iy >= 0) & (iy < r.h)
        ix, iy, q, c, tl = ix[ok], iy[ok], qs[ok], cs[ok], tol[ok]
        visible = q + tl >= r.depth[iy, ix]
        r.color[iy[visible], ix[visible]] = c[visible]


def _clip_near(seg: np.ndarray, near: float) -> np.ndarray | None:
    """Clip a camera-space segment against the near plane (z = -near)."""
    d0, d1 = -seg[0, 2], -seg[1, 2]
    if d0 < near and d1 < near:
        return None
    if d0 >= near and d1 >= near:
        return seg
    t = (near - d0) / (d1 - d0)
    hit = seg[0] + (seg[1] - seg[0]) * t
    return np.array([hit, seg[1]]) if d0 < near else np.array([seg[0], hit])
