"""Tests for the expression language."""

import math

import pytest

from pymodeler.script.expr import Env, ExprError, evaluate, parse, queries_used, variables_used

VARS = {"w": 4000.0, "h": 2.4, "pts": [1.0, 2.0, 3.0], "i": 3.0}


def env(mm_per_unit: float = 1.0) -> Env:
    return Env(variable=lambda n: VARS[n], mm_per_unit=mm_per_unit, known_variables=lambda: list(VARS))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$w / 2", 2000), ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("2 ^ 3 ^ 2", 512), ("-2 ** 2", -4),
        ("7 // 2", 3), ("7 % 4", 3), ("max($w, 10)", 4000), ("min(1, 2, 3)", 1), ("sqrt(16)", 4),
        ("round(2.567, 2)", 2.57), ("clamp(15, 0, 10)", 10), ("if($i > 2, 10, 20)", 10),
        ("$i % 2 == 1 and $w > 3", 1), ("not 0", 1), ("1 < 2 or 0", 1), ("$pts[1]", 2), ("pi", math.pi),
        ("cos(60)", 0.5), ("atan2(1, 1)", 45), ("2.4m", 2400), ("300mm", 300), ("8ft", 2438.4),
        ("6in", 152.4), ("8'", 2438.4), ("90deg", 90), ("0.5rad", math.degrees(0.5)),
    ],
)
def test_evaluate(text: str, expected: float) -> None:
    assert evaluate(text, env()) == pytest.approx(expected)


def test_unit_literals_convert_to_file_units() -> None:
    assert evaluate("2.4m + 300mm", env(mm_per_unit=1000.0)) == pytest.approx(2.7)
    assert evaluate("$h + 300mm", env(mm_per_unit=1000.0)) == pytest.approx(2.7)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("$wdth + 1", "unknown variable $wdth"), ("2 +", "unexpected end"), ("foo(1)", "unknown function"),
        ("width", "variables need a $"), ("@x", "needs a property"), ("@x.zmx", "did you mean zmax"),
        ("1/0", "division by zero"), ("(1", "expected ')'"), ("", "empty expression"), ("2 # 3", "unexpected character"),
        ("$pts[5]", "out of range"), ("sqrt(-1)", "sqrt()"),
    ],
)
def test_errors(text: str, fragment: str) -> None:
    with pytest.raises(ExprError, match=None) as info:
        evaluate(text, env())
    assert fragment in str(info.value)


def test_static_analysis() -> None:
    node = parse("$a + $b * @post[2].zmax")
    assert variables_used(node) == ["a", "b"]
    assert queries_used(node) == ["post"]


def test_queries_need_a_callback() -> None:
    with pytest.raises(ExprError):
        evaluate("@box.zmax", env())
