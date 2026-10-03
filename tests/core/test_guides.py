"""Construction guides: math, saving, drawing and snapping."""

import numpy as np
import pytest

from pymodeler.core.guides import Guide
from pymodeler.core.model import Model
from pymodeler.core.transform import scaling, translation
from pymodeler.core.vec import GeometryError
from pymodeler.io.native import model_from_dict, model_to_dict
from pymodeler.ops import primitives
from pymodeler.render.camera import Camera
from pymodeler.render.scene import GUIDE_DASHES, build_scene
from pymodeler.ui.inference import InferenceEngine
from pymodeler.ui.picking import PickScene

W, H = 800, 600


def test_guide_line_and_point_math() -> None:
    line = Guide((0, 100, 0), (5, 0, 0))
    assert np.allclose(line.direction, (1, 0, 0)) and not line.is_point
    assert np.allclose(line.closest_point((300, 0, 40)), (300, 100, 0))
    assert line.distance((300, 0, 0)) == pytest.approx(100)
    point = Guide((1, 2, 3))
    assert point.is_point and point.distance((1, 2, 7)) == pytest.approx(4)
    moved = line.transformed(translation((0, 0, 50)) @ scaling(2))
    assert np.allclose(moved.point, (0, 200, 50)) and np.allclose(moved.direction, (1, 0, 0))
    with pytest.raises(GeometryError):
        Guide((0, 0, 0), (0, 0, 0))


def test_guides_are_saved_but_not_geometry() -> None:
    model = Model()
    primitives.box(model.entities, (0, 0, 0), (1000, 1000, 1000))
    model.add_guide((0, 0, 500), (0, 1, 0))
    model.add_guide((250, 250, 250))
    loaded = model_from_dict(model_to_dict(model))
    assert [g.to_dict() for g in loaded.guides] == [g.to_dict() for g in model.guides]
    assert np.allclose(loaded.bounds()[1], (1000, 1000, 1000)), "guides do not change the model's size"
    plain = build_scene(model, axes=False, grid=False)
    with_guides = build_scene(model, axes=False, grid=False, guides=True)
    assert len(plain.helper_lines) == 0
    assert len(with_guides.helper_lines) == 2 * GUIDE_DASHES + 6, "dashes for the line, a cross for the point"


def test_inference_snaps_to_guides() -> None:
    model = Model()
    line = model.add_guide((0, 400, 0), (1, 0, 0))
    point = model.add_guide((-700, -700, 0))
    cam = Camera.standard("top", (np.array([-1000.0, -1000, 0]), np.array([1000.0, 1000, 0])), W / H,
                          perspective=False)
    engine = InferenceEngine(PickScene(model), model.guides)
    scene = PickScene(Model())

    def screen(p):
        proj = scene.project(cam, np.array([p], dtype=float), W, H)
        return float(proj.xy[0, 0]), float(proj.xy[0, 1])

    x, y = screen((300, 400, 0))
    hit = engine.infer(cam, x + 1, y + 3, W, H)
    assert hit.kind == "on_guide" and abs(hit.point[1] - 400) < 1e-6 and hit.extra["guide"] is line
    x, y = screen((-700, -700, 0))
    hit = engine.infer(cam, x + 2, y - 2, W, H)
    assert hit.kind == "guide_point" and np.allclose(hit.point, (-700, -700, 0))
    assert engine.guide_near(cam, x, y, W, H) is point
    x, y = screen((300, 0, 0))
    assert engine.infer(cam, x, y, W, H).kind == "plane"
    assert InferenceEngine(PickScene(model)).infer(cam, *screen((300, 400, 0)), W, H).kind == "plane", \
        "hidden guides are not snapped to"
