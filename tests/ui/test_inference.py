"""Tests for picking, inference, Measurements-box parsing and undo (no Qt needed)."""

import numpy as np
import pytest

from pymodeler.core.model import Model
from pymodeler.core.vec import Plane
from pymodeler.ops import primitives
from pymodeler.ops.organize import make_group
from pymodeler.render.camera import Camera
from pymodeler.ui.document import Document
from pymodeler.ui.inference import InferenceEngine
from pymodeler.ui.picking import PickScene
from pymodeler.ui.vcb import VcbError, parse_angle_text, parse_lengths, parse_segments

W, H = 800, 600


def box_model() -> Model:
    model = Model()
    primitives.box(model.entities, (0, 0, 0), (1000, 1000, 1000))
    return model


def top_camera(model: Model) -> Camera:
    return Camera.standard("top", model.bounds(), W / H, perspective=False)


def screen(camera: Camera, point) -> tuple[float, float]:
    scene = PickScene(Model())
    proj = scene.project(camera, np.array([point], dtype=float), W, H)
    return float(proj.xy[0, 0]), float(proj.xy[0, 1])


def test_pick_faces_points_and_edges() -> None:
    model = box_model()
    cam = Camera.standard("iso", model.bounds(), W / H)
    scene = PickScene(model)
    x, y = screen(cam, (500, 500, 1000))
    hits = scene.ray_faces(*_ray(cam, x, y))
    assert hits and np.allclose(hits[0].point, (500, 500, 1000), atol=1e-6)
    x, y = screen(cam, (1000, 0, 1000))
    near = scene.near_points(cam, x + 3, y - 2, W, H)
    assert near[0].kind == "vertex" and np.allclose(near[0].point, (1000, 0, 1000))
    x, y = screen(cam, (1000, 250, 1000))
    edges = scene.near_edges(cam, x + 2, y, W, H)
    assert edges and abs(edges[0].point[0] - 1000) < 1e-6


def test_picking_inside_groups_uses_world_coordinates() -> None:
    model = Model()
    primitives.box(model.entities, (0, 0, 0), (100, 100, 100))
    group = make_group(model, model.entities, [*model.entities.faces.values(), *model.entities.edges.values()])
    group.transform[:3, 3] = (2000, 0, 0)
    cam = Camera.standard("top", model.bounds(), W / H, perspective=False)
    scene = PickScene(model)
    x, y = screen(cam, (2050, 50, 100))
    hit = scene.ray_faces(*_ray(cam, x, y))[0]
    assert np.allclose(hit.point, (2050, 50, 100), atol=1e-6) and hit.path == (group,)


def _ray(cam, x, y):
    from pymodeler.ui.navigation import pixel_ray

    return pixel_ray(cam, x, y, W, H)


def test_inference_priorities() -> None:
    model = box_model()
    cam = Camera.standard("iso", model.bounds(), W / H)
    engine = InferenceEngine(PickScene(model))
    x, y = screen(cam, (0, 0, 1000))
    assert engine.infer(cam, x + 2, y + 2, W, H).kind == "endpoint"
    x, y = screen(cam, (500, 0, 1000))
    assert engine.infer(cam, x + 1, y, W, H).kind == "midpoint"
    x, y = screen(cam, (250, 0, 1000))
    assert engine.infer(cam, x, y + 1, W, H).kind == "on_edge"
    x, y = screen(cam, (400, 600, 1000))
    inf = engine.infer(cam, x, y, W, H)
    assert inf.kind == "on_face" and inf.plane is not None and np.allclose(inf.plane.normal, (0, 0, 1))
    x, y = screen(cam, (0, 0, 0))
    assert engine.infer(cam, x, y, W, H).kind in ("origin", "endpoint")


def test_axis_inference_and_locks() -> None:
    model = Model()
    cam = Camera.standard("iso", (np.zeros(3), np.full(3, 3000.0)), W / H)
    engine = InferenceEngine(PickScene(model))
    start = np.array([1000.0, 1000.0, 0.0])
    x, y = screen(cam, (2500, 1000, 0))
    inf = engine.infer(cam, x + 3, y + 3, W, H, start=start)
    assert inf.kind == "axis_x" and inf.label == "On Red Axis"
    assert abs(inf.point[1] - 1000) < 1e-6 and abs(inf.point[2]) < 1e-6
    x, y = screen(cam, (1000, 1000, 1500))
    inf = engine.infer(cam, x + 2, y, W, H, start=start)
    assert inf.kind == "axis_z"
    x, y = screen(cam, (1800, 2400, 0))
    locked = engine.infer(cam, x, y, W, H, start=start, lock=np.array([0, 1.0, 0]))
    assert locked.kind == "locked" and abs(locked.point[0] - 1000) < 1e-6


def test_fallback_to_drawing_plane() -> None:
    model = Model()
    cam = Camera.standard("iso", (np.zeros(3), np.full(3, 3000.0)), W / H)
    engine = InferenceEngine(PickScene(model))
    inf = engine.infer(cam, 300, 400, W, H)
    assert inf.kind == "plane" and abs(inf.point[2]) < 1e-6
    raised = Plane.from_point_normal((0, 0, 500), (0, 0, 1))
    inf = engine.infer(cam, 300, 400, W, H, plane=raised)
    assert abs(inf.point[2] - 500) < 1e-6


def test_parallel_inference() -> None:
    model = Model()
    model.entities.add_line((0, 0, 0), (1000, 500, 0))
    cam = top_camera(model)
    cam.ortho_height *= 4
    engine = InferenceEngine(PickScene(model))
    x, y = screen(cam, (600, 300, 0))
    assert engine.infer(cam, x, y, W, H).kind == "on_edge"
    start = np.array([0.0, 2000.0, 0.0])
    x, y = screen(cam, (2000, 3000, 0))
    inf = engine.infer(cam, x, y, W, H, start=start)
    assert inf.kind == "parallel"
    d = inf.point - start
    assert abs(d[1] / d[0] - 0.5) < 1e-6


def test_vcb_parsing() -> None:
    assert parse_lengths("2500", "mm") == [2500]
    assert parse_lengths("2.5m", "mm") == [2500]
    assert parse_lengths("2000, 1500", "mm", count=2) == [2000, 1500]
    assert parse_lengths("2", "m") == [2000]
    assert parse_lengths("8' 6\"", "mm")[0] == pytest.approx(2590.8)
    assert parse_segments("24s") == 24 and parse_segments("24") is None
    assert parse_angle_text("45") == 45
    for bad in ("", "abc", "1,2,3"):
        with pytest.raises(VcbError):
            parse_lengths(bad, "mm", count=2)
    with pytest.raises(VcbError):
        parse_segments("2s")


def test_document_undo_redo() -> None:
    doc = Document()
    doc.perform("Draw", lambda: doc.model.entities.add_line((0, 0, 0), (1000, 0, 0)))
    doc.perform("Draw", lambda: doc.model.entities.add_line((1000, 0, 0), (1000, 1000, 0)))
    assert len(doc.model.entities.edges) == 2 and doc.undo_stack.undo_name == "Draw"
    assert doc.undo()
    assert len(doc.model.entities.edges) == 1
    assert doc.redo()
    assert len(doc.model.entities.edges) == 2
    assert doc.undo() and doc.undo() and not doc.undo()
    assert len(doc.model.entities.edges) == 0
    with pytest.raises(ValueError):
        doc.perform("Bad", lambda: (doc.model.entities.add_line((0, 0, 0), (5, 0, 0)), _boom()))
    assert len(doc.model.entities.edges) == 0, "a failed command leaves no trace"


def _boom():
    raise ValueError("boom")
