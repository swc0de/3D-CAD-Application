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
out float v_t;
void main() {
    gl_Position = vec4(in_pos, 0.999, 1.0);
    v_t = (1.0 - in_pos.y) * 0.5;
}
"""

_SKY_FS = """
#version 330
uniform vec3 top;
uniform vec3 bottom;
in float v_t;
out vec4 f_color;
void main() { f_color = vec4(mix(top, bottom, v_t), 1.0); }
"""


class GLSceneRenderer:
    """Draws :class:`SceneData` with moderngl into the currently bound framebuffer."""

    def __init__(self, ctx: "object") -> None:
        import moderngl

        self._mgl = moderngl
        self.ctx = ctx
        self.mesh_prog = ctx.program(vertex_shader=_MESH_VS, fragment_shader=_MESH_FS)
        self.line_prog = ctx.program(vertex_shader=_LINE_VS, fragment_shader=_LINE_FS)
        self.sky_prog = ctx.program(vertex_shader=_SKY_VS, fragment_shader=_SKY_FS)
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

    def release_scene(self) -> None:
        """Free GPU buffers of the current scene."""
        for item in (self._opaque, self._clear, self._lines, self._helpers):
            if item is not None:
                item[0].release()
        self._opaque = self._clear = self._lines = self._helpers = None

    def draw(self, camera: Camera, width: int, height: int) -> None:
        """Render the current scene with ``camera`` into the bound framebuffer."""
        mgl = self._mgl
        ctx = self.ctx
        ctx.viewport = (0, 0, width, height)
        ctx.clear(*SKY_BOTTOM, 1.0, depth=1.0)
        ctx.disable(mgl.DEPTH_TEST | mgl.CULL_FACE)
        self.sky_prog["top"].value = tuple(float(c) for c in SKY_TOP)
        self.sky_prog["bottom"].value = tuple(float(c) for c in SKY_BOTTOM)
        self.sky_vao.render(mgl.TRIANGLE_STRIP)
        radius = self.scene.radius if self.scene is not None else 1000.0
        near, far = camera.clip_range(radius)
        view = camera.view_matrix()
        mvp = camera.projection_matrix(width / max(height, 1), near, far) @ view
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
            ctx.blend_func = mgl.SRC_ALPHA, mgl.ONE_MINUS_SRC_ALPHA
            ctx.depth_mask = False
            self._clear[0].render(mgl.TRIANGLES, vertices=self._clear[1])
            ctx.depth_mask = True
            ctx.disable(mgl.BLEND)


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
