"""OpenGL (moderngl) renderer shared by the Qt viewport and offscreen previews."""

from __future__ import annotations

import numpy as np

from pymodeler.render.camera import Camera
from pymodeler.render.scene import SceneData
from pymodeler.render.software import AMBIENT, LIGHT_DIR, SKY_BOTTOM, SKY_TOP

_MESH_VS = """
#version 330
uniform mat4 mvp;
uniform mat3 normal_view;
in vec3 in_pos;
in vec3 in_normal;
in vec4 in_color;
out vec3 v_normal;
out vec4 v_color;
void main() {
    gl_Position = mvp * vec4(in_pos, 1.0);
    v_normal = normal_view * in_normal;
    v_color = in_color;
}
"""

_MESH_FS = """
#version 330
uniform vec3 light_dir;
uniform float ambient;
in vec3 v_normal;
in vec4 v_color;
out vec4 f_color;
void main() {
    float d = max(dot(normalize(v_normal), light_dir), 0.0);
    f_color = vec4(v_color.rgb * (ambient + (1.0 - ambient) * d), v_color.a);
}
"""

_LINE_VS = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
in vec3 in_color;
out vec3 v_color;
void main() {
    gl_Position = mvp * vec4(in_pos, 1.0);
    v_color = in_color;
}
"""

_LINE_FS = """
#version 330
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, 1.0); }
"""

_SKY_VS = """
#version 330
in vec2 in_pos;
out vec2 v_ndc;
void main() {
    gl_Position = vec4(in_pos, 0.999, 1.0);
    v_ndc = in_pos;
}
"""

_SKY_FS = """
#version 330
uniform vec3 top;
uniform vec3 bottom;
uniform vec3 ground;
uniform int horizon;
uniform mat4 inv_vp;
in vec2 v_ndc;
out vec4 f_color;
void main() {
    if (horizon == 1) {
        vec4 a = inv_vp * vec4(v_ndc, -1.0, 1.0);
        vec4 b = inv_vp * vec4(v_ndc, 1.0, 1.0);
        vec3 dir = normalize(b.xyz / b.w - a.xyz / a.w);
        if (dir.z < 0.0) {
            f_color = vec4(mix(ground * 1.06, ground, clamp(-dir.z * 4.0, 0.0, 1.0)), 1.0);
        } else {
            f_color = vec4(mix(bottom, top, clamp(dir.z * 2.5, 0.0, 1.0)), 1.0);
        }
    } else {
        f_color = vec4(mix(top, bottom, (1.0 - v_ndc.y) * 0.5), 1.0);
    }
}
"""

GROUND_COLOR = (0.74, 0.75, 0.70)

_FLAT_VS = """
#version 330
uniform mat4 mvp;
in vec3 in_pos;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); }
"""

_FLAT_FS = """
#version 330
uniform vec4 color;
out vec4 f_color;
void main() { f_color = color; }
"""

HIGHLIGHT_FACE = (0.25, 0.45, 1.0, 0.35)
HIGHLIGHT_LINE = (0.1, 0.3, 1.0, 1.0)


class GLSceneRenderer:
    """Draws :class:`SceneData` with moderngl into the currently bound framebuffer."""

    def __init__(self, ctx: "object") -> None:
        import moderngl

        self._mgl = moderngl
        self.ctx = ctx
        self.mesh_prog = ctx.program(vertex_shader=_MESH_VS, fragment_shader=_MESH_FS)
        self.line_prog = ctx.program(vertex_shader=_LINE_VS, fragment_shader=_LINE_FS)
        self.sky_prog = ctx.program(vertex_shader=_SKY_VS, fragment_shader=_SKY_FS)
        self.flat_prog = ctx.program(vertex_shader=_FLAT_VS, fragment_shader=_FLAT_FS)
        self._hl_tris = self._hl_lines = None
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
        self.sky_vao = ctx.vertex_array(self.sky_prog, [(ctx.buffer(quad.tobytes()), "2f", "in_pos")])
        self._opaque = self._clear = self._lines = self._helpers = None
        self.scene: SceneData | None = None

    def set_scene(self, scene: SceneData) -> None:
        """Upload a scene's geometry to the GPU."""
        self.release_scene()
        self.scene = scene
        tri_alpha = scene.colors[0::3, 3] if len(scene.colors) else np.zeros(0)
        opaque_tris = np.flatnonzero(tri_alpha >= 0.999)
        clear_tris = np.flatnonzero(tri_alpha < 0.999)
        self._opaque = self._mesh_vao(scene, opaque_tris)
        self._clear = self._mesh_vao(scene, clear_tris)
        self._lines = self._line_vao(scene.lines, scene.line_colors)
        self._helpers = self._line_vao(scene.helper_lines, scene.helper_colors)

    def _mesh_vao(self, scene: SceneData, tris: np.ndarray) -> tuple[object, int] | None:
        """Vertex array for a subset of triangles."""
        if not len(tris):
            return None
        rows = (tris[:, None] * 3 + np.arange(3)[None, :]).reshape(-1)
        data = np.hstack([scene.positions[rows], scene.normals[rows], scene.colors[rows]]).astype("f4")
        buf = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.mesh_prog, [(buf, "3f 3f 4f", "in_pos", "in_normal", "in_color")])
        return vao, len(rows)

    def _line_vao(self, pts: np.ndarray, cols: np.ndarray) -> tuple[object, int] | None:
        """Vertex array for line segments."""
        if not len(pts):
            return None
        data = np.hstack([pts, cols]).astype("f4")
        buf = self.ctx.buffer(data.tobytes())
        vao = self.ctx.vertex_array(self.line_prog, [(buf, "3f 3f", "in_pos", "in_color")])
        return vao, len(pts)

    def set_highlight(self, triangles: np.ndarray, lines: np.ndarray) -> None:
        """Upload selection highlight geometry (triangle corners and line points)."""
        for item in (self._hl_tris, self._hl_lines):
            if item is not None:
                item[0].release()
        self._hl_tris = self._flat_vao(triangles)
        self._hl_lines = self._flat_vao(lines)

    def _flat_vao(self, pts: np.ndarray) -> tuple[object, int] | None:
        if not len(pts):
            return None
        buf = self.ctx.buffer(np.ascontiguousarray(pts, dtype="f4").tobytes())
        return self.ctx.vertex_array(self.flat_prog, [(buf, "3f", "in_pos")]), len(pts)

    def _draw_highlight(self, mvp_bytes: bytes) -> None:
        mgl = self._mgl
        ctx = self.ctx
        if self._hl_tris is None and self._hl_lines is None:
            return
        self.flat_prog["mvp"].write(mvp_bytes)
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA, mgl.ZERO, mgl.ONE  # keep alpha = 1 for Qt compositing
        ctx.depth_func = "<="
        ctx.depth_mask = False
        if self._hl_tris is not None:
            ctx.disable(mgl.CULL_FACE)
            ctx.polygon_offset = (-1.0, -1.0)
            self.flat_prog["color"].value = HIGHLIGHT_FACE
            self._hl_tris[0].render(mgl.TRIANGLES, vertices=self._hl_tris[1])
            ctx.polygon_offset = (0.0, 0.0)
        if self._hl_lines is not None:
            self.flat_prog["color"].value = HIGHLIGHT_LINE
            self._hl_lines[0].render(mgl.LINES, vertices=self._hl_lines[1])
        ctx.depth_mask = True
        ctx.depth_func = "<"
        ctx.disable(mgl.BLEND)

    def release_scene(self) -> None:
        """Free GPU buffers of the current scene."""
        for item in (self._opaque, self._clear, self._lines, self._helpers):
            if item is not None:
                item[0].release()
        self._opaque = self._clear = self._lines = self._helpers = None

    def draw(self, camera: Camera, width: int, height: int, horizon: bool = False) -> None:
        """Render the current scene with ``camera`` into the bound framebuffer.

        ``horizon`` draws SketchUp-style sky above and ground below the horizon
        (perspective only); otherwise the background is a flat gradient.
        """
        mgl = self._mgl
        ctx = self.ctx
        ctx.viewport = (0, 0, width, height)
        ctx.clear(*SKY_BOTTOM, 1.0, depth=1.0)
        ctx.disable(mgl.DEPTH_TEST | mgl.CULL_FACE | mgl.BLEND)
        radius = self.scene.radius if self.scene is not None else 1000.0
        near, far = camera.clip_range(radius)
        view = camera.view_matrix()
        mvp = camera.projection_matrix(width / max(height, 1), near, far) @ view
        self.sky_prog["top"].value = tuple(float(c) for c in SKY_TOP)
        self.sky_prog["bottom"].value = tuple(float(c) for c in SKY_BOTTOM)
        self.sky_prog["ground"].value = GROUND_COLOR
        use_horizon = horizon and camera.perspective
        self.sky_prog["horizon"].value = 1 if use_horizon else 0
        if use_horizon:
            self.sky_prog["inv_vp"].write(np.linalg.inv(mvp).T.astype("f4").tobytes())
        self.sky_vao.render(mgl.TRIANGLE_STRIP)
        mvp_bytes = mvp.T.astype("f4").tobytes()
        ctx.enable(mgl.DEPTH_TEST | mgl.CULL_FACE)
        ctx.front_face = "ccw"
        self.mesh_prog["mvp"].write(mvp_bytes)
        self.mesh_prog["normal_view"].write(view[:3, :3].T.astype("f4").tobytes())
        light = LIGHT_DIR / np.linalg.norm(LIGHT_DIR)
        self.mesh_prog["light_dir"].value = tuple(float(c) for c in light)
        self.mesh_prog["ambient"].value = AMBIENT
        ctx.polygon_offset = (1.0, 1.0)
        if self._opaque is not None:
            self._opaque[0].render(mgl.TRIANGLES, vertices=self._opaque[1])
        ctx.polygon_offset = (0.0, 0.0)
        self.line_prog["mvp"].write(mvp_bytes)
        ctx.depth_func = "<="
        for item in (self._helpers, self._lines):
            if item is not None:
                item[0].render(mgl.LINES, vertices=item[1])
        ctx.depth_func = "<"
        if self._clear is not None:
            ctx.enable(mgl.BLEND)
            ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA, mgl.ZERO, mgl.ONE  # keep alpha = 1 for Qt compositing
            ctx.depth_mask = False
            self._clear[0].render(mgl.TRIANGLES, vertices=self._clear[1])
            ctx.depth_mask = True
            ctx.disable(mgl.BLEND)
        self._draw_highlight(mvp_bytes)


_STANDALONE: object | None = None
_STANDALONE_FAILED: str | None = None


def standalone_context() -> object | None:
    """A cached headless moderngl context (EGL first, then the platform default).

    Returns ``None`` when no OpenGL 3.3 context can be created on this machine.
    """
    global _STANDALONE, _STANDALONE_FAILED
    if _STANDALONE is not None or _STANDALONE_FAILED is not None:
        return _STANDALONE
    try:
        import moderngl
    except ImportError as exc:  # pragma: no cover - moderngl is a dependency
        _STANDALONE_FAILED = str(exc)
        return None
    errors = []
    for kwargs in ({"backend": "egl"}, {}):
        try:
            _STANDALONE = moderngl.create_standalone_context(require=330, **kwargs)
            return _STANDALONE
        except Exception as exc:  # noqa: BLE001 - any failure means "no GL here"
            errors.append(f"{kwargs or 'default'}: {exc}")
    _STANDALONE_FAILED = "; ".join(errors)
    return None


def render_gl_offscreen(scene: SceneData, camera: Camera, width: int, height: int) -> np.ndarray | None:
    """Render to an RGB uint8 image with a headless GL context (``None`` if unavailable)."""
    ctx = standalone_context()
    if ctx is None:
        return None
    samples = min(4, getattr(ctx, "max_samples", 0) or 0)
    color_ms = ctx.renderbuffer((width, height), 4, samples=samples)
    depth_ms = ctx.depth_renderbuffer((width, height), samples=samples)
    fbo_ms = ctx.framebuffer(color_ms, depth_ms)
    color = ctx.renderbuffer((width, height), 4)
    fbo = ctx.framebuffer(color)
    renderer = GLSceneRenderer(ctx)
    try:
        fbo_ms.use()
        renderer.set_scene(scene)
        renderer.draw(camera, width, height)
        ctx.copy_framebuffer(fbo, fbo_ms)
        data = fbo.read(components=3, alignment=1)
        img = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
        return np.flipud(img).copy()
    finally:
        renderer.release_scene()
        for obj in (fbo_ms, color_ms, depth_ms, fbo, color):
            obj.release()
