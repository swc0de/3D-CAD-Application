"""Tests for colours, materials and tags."""

import pytest

from pymodeler.core.materials import ColorError, Material, color_to_hex, parse_color
from pymodeler.core.tags import Tag


def test_parse_hex_and_names() -> None:
    assert parse_color("#ff0000") == ((1.0, 0.0, 0.0), None)
    assert parse_color("#f00")[0] == (1.0, 0.0, 0.0)
    rgb, alpha = parse_color("#00000080")
    assert rgb == (0.0, 0.0, 0.0) and alpha == pytest.approx(128 / 255)
    assert color_to_hex(parse_color("brick")[0]) == "#a0522d"


def test_parse_lists() -> None:
    assert parse_color([255, 0, 0]) == ((1.0, 0.0, 0.0), None)
    assert parse_color([0.5, 0.5, 0.5, 0.25]) == ((0.5, 0.5, 0.5), 0.25)


@pytest.mark.parametrize("bad", ["notacolor", "#12", [1, 2], ["a", "b", "c"], 42])
def test_bad_colors(bad: object) -> None:
    with pytest.raises(ColorError):
        parse_color(bad)


def test_material_from_spec() -> None:
    glass = Material.from_spec("glass", {"color": "#88ccff", "opacity": 0.4})
    assert glass.opacity == 0.4 and glass.is_transparent
    assert Material.from_spec("red", "red").opacity == 1.0
    with pytest.raises(ColorError):
        Material.from_spec("x", {"color": "#fff", "opacity": 2})


def test_tag_from_spec() -> None:
    tag = Tag.from_spec("Walls", {"visible": False, "color": "#ff0000"})
    assert not tag.visible and tag.color == (1.0, 0.0, 0.0)
    assert Tag.from_spec("Roof", None).visible
