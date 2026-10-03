"""A small, safe expression language for build-script values.

Examples::

    "$width / 2"            variables are written with a dollar sign
    "2.4m + 300mm"          numbers may carry units (converted to the file's units)
    "@table.zmax + 10"      geometry queries read earlier objects' bounding boxes
    "max($a, $b) * 2"       functions: sin cos tan asin acos atan atan2 sqrt abs min max
                            round floor ceil pow hypot clamp if
    "$i % 2 == 0 and $n > 3"  comparisons and boolean logic give 1 or 0

Expressions are parsed into a tiny AST and evaluated against callbacks, so they can be
checked statically (unknown variables, syntax errors) before a model is built.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pymodeler.core.units import ANGLE_UNITS, MM_PER_UNIT, is_angle_unit, is_length_unit, normalize_unit


class ExprError(ValueError):
    """Raised for syntax errors and evaluation failures."""


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?:\s*(?:mm|cm|m|in|ft|deg|rad)\b|\s*["'°])?)
  | (?P<var>\$[A-Za-z_][A-Za-z0-9_]*)
  | (?P<query>@[A-Za-z_][A-Za-z0-9_\-]*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|//|<=|>=|==|!=|&&|\|\||[-+*/%^()<>!,.\[\]])
    """,
    re.VERBOSE,
)

_NUM_UNIT_RE = re.compile(r"^((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*)$")

QUERY_PROPS = (
    "xmin", "xmax", "ymin", "ymax", "zmin", "zmax",
    "xmid", "ymid", "zmid", "width", "depth", "height",
)
"""Bounding-box properties available as ``@name.prop``."""

FUNCTIONS: dict[str, Callable[..., float]] = {
    "sin": lambda a: math.sin(math.radians(a)),
    "cos": lambda a: math.cos(math.radians(a)),
    "tan": lambda a: math.tan(math.radians(a)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "round": lambda x, n=0: round(x, int(n)),
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": math.pow,
    "hypot": math.hypot,
    "clamp": lambda x, lo, hi: max(lo, min(hi, x)),
    "if": lambda c, a, b: a if c else b,
    "int": lambda x: int(x),
}
"""Functions callable in expressions (trigonometry uses degrees)."""

CONSTANTS = {"pi": math.pi, "e": math.e, "true": 1.0, "false": 0.0}


@dataclass
class Env:
    """Evaluation callbacks.

    Attributes:
        variable: Returns a variable's value (raises KeyError if unknown).
        query: Returns ``@name.prop`` in file units.
        mm_per_unit: Size of one file unit in millimetres (for unit literals).
    """

    variable: Callable[[str], Any]
    query: Callable[[str, int | None, str], float] = field(default_factory=lambda: _no_query)
    mm_per_unit: float = 1.0
    known_variables: Callable[[], list[str]] = field(default=lambda: [])


def _no_query(name: str, index: int | None, prop: str) -> float:
    """Default query callback for contexts without geometry."""
    raise ExprError(f"geometry query @{name}.{prop} is not available here")


# AST nodes are tuples: ("num", value, unit) | ("var", name) | ("query", name, index_node, prop)
# | ("call", name, [args]) | ("const", name) | ("neg", node) | ("not", node)
# | ("bin", op, left, right) | ("index", node, index_node)
Node = tuple


def parse(text: str) -> Node:
    """Parse an expression into an AST.

    Raises:
        ExprError: on a syntax error (the message shows where).
    """
    return _Parser(text).parse()


def evaluate(text: str | Node, env: Env) -> Any:
    """Parse (if needed) and evaluate an expression."""
    node = parse(text) if isinstance(text, str) else text
    return _eval(node, env)


def variables_used(node: Node) -> list[str]:
    """Variable names referenced by an AST (without the ``$``)."""
    out: list[str] = []
    _walk(node, lambda n: out.append(n[1]) if n[0] == "var" else None)
    return out


def queries_used(node: Node) -> list[str]:
    """Object names referenced by ``@name.prop`` queries."""
    out: list[str] = []
    _walk(node, lambda n: out.append(n[1]) if n[0] == "query" else None)
    return out


def _walk(node: Node, visit: Callable[[Node], None]) -> None:
    visit(node)
    for child in node[1:]:
        if isinstance(child, tuple):
            _walk(child, visit)
        elif isinstance(child, list):
            for c in child:
                if isinstance(c, tuple):
                    _walk(c, visit)


class _Parser:
    """Recursive-descent parser."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = self._tokenize(text)
        self.pos = 0

    def _tokenize(self, text: str) -> list[tuple[str, str, int]]:
        tokens = []
        i = 0
        while i < len(text):
            m = _TOKEN_RE.match(text, i)
            if not m:
                raise ExprError(f"unexpected character {text[i]!r} at position {i + 1} in {text!r}")
            kind = m.lastgroup or ""
            if kind != "ws":
                tokens.append((kind, m.group(), i))
            i = m.end()
        tokens.append(("end", "", len(text)))
        return tokens

    def peek(self) -> tuple[str, str, int]:
        return self.tokens[self.pos]

    def take(self) -> tuple[str, str, int]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, value: str) -> None:
        kind, text, at = self.take()
        if text != value:
            found = "end of expression" if kind == "end" else repr(text)
            raise ExprError(f"expected {value!r} but found {found} at position {at + 1} in {self.text!r}")

    def parse(self) -> Node:
        if self.peek()[0] == "end":
            raise ExprError("empty expression")
        node = self.or_expr()
        kind, text, at = self.peek()
        if kind != "end":
            raise ExprError(f"unexpected {text!r} at position {at + 1} in {self.text!r}")
        return node

    def or_expr(self) -> Node:
        node = self.and_expr()
        while self.peek()[1] in ("or", "||"):
            self.take()
            node = ("bin", "or", node, self.and_expr())
        return node

    def and_expr(self) -> Node:
        node = self.not_expr()
        while self.peek()[1] in ("and", "&&"):
            self.take()
            node = ("bin", "and", node, self.not_expr())
        return node

    def not_expr(self) -> Node:
        if self.peek()[1] in ("not", "!"):
            self.take()
            return ("not", self.not_expr())
        return self.comparison()

    def comparison(self) -> Node:
        node = self.additive()
        if self.peek()[1] in ("<", "<=", ">", ">=", "==", "!="):
            op = self.take()[1]
            node = ("bin", op, node, self.additive())
        return node

    def additive(self) -> Node:
        node = self.multiplicative()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.multiplicative())
        return node

    def multiplicative(self) -> Node:
        node = self.unary()
        while self.peek()[1] in ("*", "/", "%", "//"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.peek()[1] == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek()[1] == "+":
            self.take()
            return self.unary()
        return self.power()

    def power(self) -> Node:
        node = self.postfix()
        if self.peek()[1] in ("^", "**"):
            self.take()
            return ("bin", "**", node, self.unary())
        return node

    def postfix(self) -> Node:
        node = self.atom()
        while self.peek()[1] == "[":
            self.take()
            index = self.or_expr()
            self.expect("]")
            node = ("index", node, index)
        return node

    def atom(self) -> Node:
        kind, text, at = self.take()
        if kind == "num":
            m = _NUM_UNIT_RE.match(text)
            assert m is not None
            return ("num", float(m.group(1)), m.group(2).strip())
        if kind == "var":
            return ("var", text[1:])
        if kind == "query":
            return self.query(text[1:], at)
        if kind == "name":
            if self.peek()[1] == "(":
                return self.call(text, at)
            if text in CONSTANTS:
                return ("const", text)
            raise ExprError(
                f"unknown name {text!r} at position {at + 1} in {self.text!r} "
                "(variables need a $, e.g. $width)"
            )
        if text == "(":
            node = self.or_expr()
            self.expect(")")
            return node
        found = "end of expression" if kind == "end" else repr(text)
        raise ExprError(f"unexpected {found} at position {at + 1} in {self.text!r}")

    def query(self, name: str, at: int) -> Node:
        index = None
        if self.peek()[1] == "[":
            self.take()
            index = self.or_expr()
            self.expect("]")
        if self.peek()[1] != ".":
            raise ExprError(f"geometry query @{name} needs a property, e.g. @{name}.zmax")
        self.take()
        kind, prop, pat = self.take()
        if kind != "name" or prop not in QUERY_PROPS:
            hint = difflib.get_close_matches(prop, QUERY_PROPS, 1)
            extra = f" (did you mean {hint[0]}?)" if hint else f" (use one of: {', '.join(QUERY_PROPS)})"
            raise ExprError(f"unknown query property {prop!r} at position {pat + 1}{extra}")
        return ("query", name, index, prop)

    def call(self, name: str, at: int) -> Node:
        if name not in FUNCTIONS:
            hint = difflib.get_close_matches(name, FUNCTIONS, 1)
            extra = f" (did you mean {hint[0]}?)" if hint else ""
            raise ExprError(f"unknown function {name!r} at position {at + 1}{extra}")
        self.expect("(")
        args: list[Node] = []
        if self.peek()[1] != ")":
            args.append(self.or_expr())
            while self.peek()[1] == ",":
                self.take()
                args.append(self.or_expr())
        self.expect(")")
        return ("call", name, args)


def _eval(node: Node, env: Env) -> Any:
    kind = node[0]
    if kind == "num":
        return _num_value(node[1], node[2], env)
    if kind == "var":
        try:
            return env.variable(node[1])
        except KeyError:
            known = env.known_variables()
            hint = difflib.get_close_matches(node[1], known, 1)
            extra = f" (did you mean ${hint[0]}?)" if hint else (
                f" (defined: {', '.join('$' + k for k in known)})" if known else "")
            raise ExprError(f"unknown variable ${node[1]}{extra}") from None
    if kind == "const":
        return CONSTANTS[node[1]]
    if kind == "query":
        index = None if node[2] is None else int(_number(_eval(node[2], env)))
        return env.query(node[1], index, node[3])
    if kind == "neg":
        return -_number(_eval(node[1], env))
    if kind == "not":
        return 0.0 if _number(_eval(node[1], env)) else 1.0
    if kind == "index":
        seq = _eval(node[1], env)
        idx = int(_number(_eval(node[2], env)))
        if not isinstance(seq, (list, tuple)):
            raise ExprError("only lists can be indexed")
        if not -len(seq) <= idx < len(seq):
            raise ExprError(f"index {idx} out of range for a list of {len(seq)}")
        return seq[idx]
    if kind == "call":
        args = [_number(_eval(a, env)) for a in node[2]]
        try:
            return float(FUNCTIONS[node[1]](*args))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExprError(f"{node[1]}(): {exc}") from None
    if kind == "bin":
        return _binary(node[1], _number(_eval(node[2], env)), lambda: _number(_eval(node[3], env)))
    raise ExprError(f"bad expression node {kind!r}")  # pragma: no cover


def _num_value(value: float, unit: str, env: Env) -> float:
    """A literal converted to file units (lengths) or degrees (angles)."""
    if not unit:
        return value
    if unit in ('"', "'"):
        return value * MM_PER_UNIT["in" if unit == '"' else "ft"] / env.mm_per_unit
    if unit == "°" or is_angle_unit(unit):
        return value * ANGLE_UNITS["deg" if unit == "°" else unit.lower()]
    if is_length_unit(unit):
        return value * MM_PER_UNIT[normalize_unit(unit)] / env.mm_per_unit
    raise ExprError(f"unknown unit {unit!r}")  # pragma: no cover


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise ExprError(f"expected a number, got {value!r}")


def _binary(op: str, a: float, b_thunk: Callable[[], float]) -> float:
    if op == "and":
        return 1.0 if a and b_thunk() else 0.0
    if op == "or":
        return 1.0 if a or b_thunk() else 0.0
    b = b_thunk()
    try:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "//":
            return float(a // b)
        if op == "%":
            return a % b
        if op == "**":
            return float(a**b)
    except ZeroDivisionError:
        raise ExprError("division by zero") from None
    except OverflowError:
        raise ExprError("number too large") from None
    comparisons = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": abs(a - b) <= 1e-9, "!=": abs(a - b) > 1e-9}
    return 1.0 if comparisons[op] else 0.0
