"""Tests for cameras, scene building, the software rasterizer and previews."""

import numpy as np
import pytest
from PIL import Image

from pymodeler.core.materials import Material
from pymodeler.core.model import Model
from pymodeler.ops import primitives
from pymodeler.render.camera import Camera
from pymodeler.render.gl_renderer import render_gl_offscreen, standalone_context
from pymodeler.render.offscreen import dimension_text, render_previews, render_view
from pymodeler.render.scene import build_scene, nice_step
from pymodeler.render.software import SKY_BOTTOM, SKY_TOP, render_software


def red_box_model() -> Model:
    model = Model(units="m")
    model.add_material(Material("red", (1.0, 0.0, 0.0)))
    for f in primitives.box(model.entities, (0, 0, 0), (2000, 1000, 1000)):
        f.material = "red"
    return model


def reddish(img: np.ndarray) -> np.ndarray:
    r, g, b = (img[..., i].astype(int) for i in range(3))
    return (r > 90) & (g < 60) & (b < 60)


def test_camera_projection_puts_target_at_center() -> None:
    cam = Camera.standard("iso", (np.zeros(3), np.full(3, 1000.0)), 4 / 3)
    clip = cam.projection_matrix(4 / 3, *cam.clip_range(1000)) @ cam.view_matrix() @ np.array([*cam.target, 1.0])
    assert np.allclose(clip[:2] / clip[3], 0, atol=1e-9)
    with pytest.raises(ValueError):
        Camera.standard("sideways", None, 1.0)


def test_framing_keeps_all_corners_visible() -> None:
    lo, hi = np.array([-500.0, 0, 0]), np.array([4000.0, 3000, 2500])
    for view in ("iso", "front", "top", "right"):
        cam = Camera.standard(view, (lo, hi), 4 / 3)
        mvp = cam.projection_matrix(4 / 3, *cam.clip_range(3000)) @ cam.view_matrix()
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    c = mvp @ np.array([x, y, z, 1.0])
                    assert np.all(np.abs(c[:2] / c[3]) <= 1.0 + 1e-9), view


def test_scene_contains_both_sides_edges_and_helpers() -> None:
    scene = build_scene(red_box_model())
    assert scene.triangle_count == 24  # 12 front + 12 back
    assert len(scene.lines) == 24  # 12 edges
    assert len(scene.helper_lines) > 6
    assert scene.grid_spacing == nice_step(2000) and nice_step(2000) in (100, 200, 500)


def test_software_render_shows_model_over_sky() -> None:
    scene = build_scene(red_box_model())
    cam = Camera.standard("front", scene.bounds, 4 / 3)
    img = render_software(scene, cam, 160, 120)
    assert img.shape == (120, 160, 3) and img.dtype == np.uint8
    assert reddish(img).mean() > 0.1
    top_left = img[0, 0].astype(float) / 255
    assert np.allclose(top_left, SKY_TOP, atol=0.05)
    assert not np.allclose(SKY_TOP, SKY_BOTTOM)


def test_software_render_handles_empty_and_transparent() -> None:
    empty = build_scene(Model())
    img = render_software(empty, Camera.standard("iso", empty.bounds, 1.0), 64, 64)
    assert img.shape == (64, 64, 3)
    model = red_box_model()
    model.materials["red"].opacity = 0.3
    scene = build_scene(model)
    img = render_software(scene, Camera.standard("front", scene.bounds, 1.0), 96, 96)
    assert reddish(img).mean() < 0.05, "a 30% opaque red box is mostly sky-coloured"


def test_render_view_software_and_auto() -> None:
    scene = build_scene(red_box_model())
    img, used = render_view(scene, "top", 120, 90, renderer="software")
    assert used == "software" and reddish(img).mean() > 0.1
    img, used = render_view(scene, "top", 120, 90, renderer="auto")
    assert used in ("gl", "software") and reddish(img).mean() > 0.1
    with pytest.raises(ValueError):
        render_view(scene, "top", 10, 10, renderer="raytracer")


@pytest.mark.skipif(standalone_context() is None, reason="no headless OpenGL context")
def test_gl_and_software_agree() -> None:
    scene = build_scene(red_box_model())
    cam = Camera.standard("iso", scene.bounds, 4 / 3)
    gl = render_gl_offscreen(scene, cam, 160, 120)
    sw = render_software(scene, cam, 160, 120)
    assert gl is not None
    assert abs(reddish(gl).mean() - reddish(sw).mean()) < 0.02


def test_render_previews_writes_views_and_sheet(tmp_path) -> None:
    result = render_previews(red_box_model(), tmp_path / "box.png", size=(160, 120), renderer="software")
    assert result.sheet.exists()
    assert set(result.views) == {"iso", "front", "top", "right"}
    assert all(p.exists() for p in result.views.values())
    sheet = Image.open(result.sheet)
    assert sheet.size[0] == 2 * 160 + 4
    with pytest.raises(ValueError):
        render_previews(red_box_model(), tmp_path / "x.png", views=["diagonal"])


def test_dimension_text_uses_model_units() -> None:
    scene = build_scene(red_box_model())
    assert dimension_text(scene, "m").startswith("W 2m x D 1m x H 1m")
    assert dimension_text(build_scene(Model()), "mm") == "(empty model)"


@pytest.mark.skipif(standalone_context() is None, reason="no headless OpenGL context")
def test_highlight_and_transparency_keep_framebuffer_opaque() -> None:
    """Qt composites the GL widget using its alpha channel, so blending must leave it at 1."""
    from pymodeler.render.gl_renderer import GLSceneRenderer
    from pymodeler.render.scene import highlight_geometry

    ctx = standalone_context()
    model = red_box_model()
    model.materials["red"].opacity = 0.5
    faces = list(model.entities.faces.values())
    scene = build_scene(model)
    color = ctx.renderbuffer((32, 32), 4)
    depth = ctx.depth_renderbuffer((32, 32))
    fbo = ctx.framebuffer(color, depth)
    fbo.use()
    renderer = GLSceneRenderer(ctx)
    renderer.set_scene(scene)
    renderer.set_highlight(*highlight_geometry(faces))
    renderer.draw(Camera.standard("top", scene.bounds, 1.0), 32, 32)
    pixels = np.frombuffer(fbo.read(components=4), dtype=np.uint8).reshape(32, 32, 4)
    assert pixels[..., 3].min() == 255
    assert pixels[16, 16, 2] > pixels[16, 16, 1], "the selected face is tinted blue"
    renderer.release_scene()
    for obj in (fbo, color, depth):
        obj.release()
