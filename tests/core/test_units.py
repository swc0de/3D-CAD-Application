"""Tests for unit parsing and formatting."""

import math

import pytest

from pymodeler.core.units import (
    UnitError,
    format_length,
    from_mm,
    parse_angle,
    parse_length,
    to_mm,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2.4m", 2400.0),
        ("300mm", 300.0),
        ("300 mm", 300.0),
        ("30cm", 300.0),
        ("8ft", 2438.4),
        ("6in", 152.4),
        ("6\"", 152.4),
        ("8'", 2438.4),
        ("8' 6\"", 2590.8),
        ("-1.5m", -1500.0),
        ("1e3mm", 1000.0),
        ("2 meters", 2000.0),
    ],
)
def test_parse_length(text: str, expected: float) -> None:
    assert math.isclose(parse_length(text), expected, rel_tol=1e-12)


def test_parse_length_default_unit() -> None:
    assert parse_length(2.5, "m") == 2500.0
    assert parse_length("2.5", "m") == 2500.0
    assert parse_length(100) == 100.0


@pytest.mark.parametrize("bad", ["", "abc", "2 parsecs", "1.2.3m"])
def test_parse_length_errors(bad: str) -> None:
    with pytest.raises(UnitError):
        parse_length(bad)


def test_conversions_round_trip() -> None:
    assert math.isclose(from_mm(to_mm(3.0, "ft"), "ft"), 3.0)
    with pytest.raises(UnitError):
        to_mm(1.0, "furlong")


def test_parse_angle() -> None:
    assert parse_angle(90) == 90.0
    assert parse_angle("45deg") == 45.0
    assert math.isclose(parse_angle("3.141592653589793rad"), 180.0)
    with pytest.raises(UnitError):
        parse_angle("10mm")


def test_format_length() -> None:
    assert format_length(2500, "m") == "2.5m"
    assert format_length(300, "mm") == "300mm"
    assert format_length(2590.8, "ft") == "8' 6\""
    assert format_length(152.4, "in") == "6\""
