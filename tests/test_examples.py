"""Every example script must validate, build, and produce the expected model."""

from pathlib import Path

import pytest

from pymodeler.core.analysis import is_closed_manifold
from pymodeler.render.offscreen import render_view
from pymodeler.render.scene import build_scene
from pymodeler.script.engine import build_file, load_script
from pymodeler.script.report import build_report
from pymodeler.script.validate import validate_script

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.json"))

EXPECTED_SIZE = {
    "01_box": [1000, 600, 400],
    "02_table": [1600, 900, 750],
    "03_chair": [450, 440, 900],
    "04_house": [8800, 6800, 5100],
    "06_fence": [7330, 175, 1250],
    "07_bookshelf": [900, 300, 1800],
    "08_tower": [6, 6, 15.5],
    "09_vase": [220, 220, 350],
    "10_aqueduct": [13000, 1200, 4550],
}


def test_there_are_at_least_eight_examples() -> None:
    assert len(EXAMPLES) >= 8


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_builds(path: Path) -> None:
    assert validate_script(load_script(path)) == []
    result = build_file(path)
    report = build_report(result)
    if path.stem in EXPECTED_SIZE:
        assert report["bounds"]["size"] == pytest.approx(EXPECTED_SIZE[path.stem], abs=1e-6)
    # Every group and component in the examples is a closed solid (except the flag face).
    for definition in result.model.definitions.values():
        if definition.entities.faces and definition.name not in ("Flag",):
            assert is_closed_manifold(definition.entities), definition.name
    image, _ = render_view(build_scene(result.model), "iso", 96, 72, renderer="software")
    assert image.std() > 5, "the preview shows something"


def test_house_has_openings_and_glass() -> None:
    result = build_file(next(p for p in EXAMPLES if p.stem == "04_house"))
    report = build_report(result)
    assert report["objects"]["walls"]["solid"]
    walls = result.refs["walls"][0][0].definition.entities
    front = [f for f in walls.faces.values() if f.normal[1] < -0.99 and f.centroid()[1] < 1]
    assert len(front) == 1 and len(front[0].loops) == 3, "door notch plus two window holes"
    assert result.model.materials["glass"].opacity < 1
