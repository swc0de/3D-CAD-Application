"""Validate build scripts with messages that point at the failing step.

Validation runs in two passes:

1. **Structure**, against the JSON Schema: each step is checked against its own op's
   schema, so mistakes read like ``step 4 (push_pull): missing required parameter
   'distance'`` instead of a wall of ``oneOf`` noise.
2. **Meaning**, statically: expressions must parse, ``$variables`` must be defined, and
   targets, ``@queries``, components and ``in`` must refer to ids defined by earlier steps.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable

import jsonschema

from pymodeler.ops.registry import ALIASES, PARAM_TYPES, OpSpec, get_op
from pymodeler.script.engine import COMMON_KEYS
from pymodeler.script.errors import Problem
from pymodeler.script.expr import ExprError, parse, queries_used, variables_used
from pymodeler.script.schema import build_schema

_NUMERIC = {"length", "number", "integer", "angle", "bool"}
_POINTY = {"point", "vector", "direction", "size", "scale", "anchor"}

_SCHEMA_CACHE: dict[str, Any] = {}


def _schema() -> dict[str, Any]:
    if "schema" not in _SCHEMA_CACHE:
        _SCHEMA_CACHE["schema"] = build_schema()
    return _SCHEMA_CACHE["schema"]


def validate_script(data: Any) -> list[Problem]:
    """All problems found in a build script (empty when it is valid)."""
    if not isinstance(data, dict):
        return [Problem("", "a build script must be a JSON object with \"version\" and \"steps\"")]
    problems = _check_top_level(data)
    steps = data.get("steps")
    if isinstance(steps, list):
        scope = _Scope(set((data.get("variables") or {}).keys()) if isinstance(data.get("variables"), dict) else set())
        _check_variables(data.get("variables"), scope, problems)
        _check_steps(steps, "", scope, problems)
    return problems


# ---------------------------------------------------------------------- structure


def _check_top_level(data: dict[str, Any]) -> list[Problem]:
    schema = dict(_schema())
    shallow = dict(schema)
    shallow["properties"] = dict(schema["properties"])
    shallow["properties"]["steps"] = {"type": "array"}
    problems = []
    for err in sorted(jsonschema.Draft202012Validator(shallow).iter_errors(data), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in err.path)
        if err.validator == "required":
            missing = err.message.split("'")[1]
            hint = ' (e.g. "version": 1)' if missing == "version" else ' (a list of steps)'
            problems.append(Problem(where, f"missing top-level key '{missing}'{hint}"))
        elif err.validator == "additionalProperties":
            extra = _extra_keys(err)
            known = list(schema["properties"])
            problems.append(Problem(where, "unknown top-level key " + ", ".join(
                f"'{k}'{_did_you_mean(k, known)}" for k in extra)))
        elif err.validator == "const" and list(err.path) == ["version"]:
            problems.append(Problem("version", "unsupported version (use 1)"))
        elif err.validator == "enum" and list(err.path) == ["units"]:
            problems.append(Problem("units", f"unknown units {err.instance!r} (use mm, cm, m, in or ft)"))
        else:
            problems.append(Problem(where, _short(err.message)))
    return problems


def _check_steps(steps: list[Any], prefix: str, scope: "_Scope", problems: list[Problem]) -> None:
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            problems.append(Problem(_join(prefix, f"step {index}"), "each step must be an object with an \"op\""))
            continue
        op_name = step.get("op")
        label = f"step {index} ({op_name}" + (f" '{step['id']}'" if step.get("id") else "") + ")"
        path = _join(prefix, label)
        if not isinstance(op_name, str):
            problems.append(Problem(_join(prefix, f"step {index}"), "missing \"op\" (the operation to run)"))
            continue
        spec = get_op(op_name)
        if spec is None:
            from pymodeler.ops.registry import all_ops

            names = [o.name for o in all_ops()] + list(ALIASES)
            problems.append(Problem(path, f"unknown op '{op_name}'{_did_you_mean(op_name, names)}"))
            continue
        _check_structure(step, spec, path, problems)
        loop_vars = _loop_variables(step.get("repeat"))
        scope.push(loop_vars)
        try:
            _check_meaning(step, spec, path, scope, problems)
            for key in ("steps",):
                if isinstance(step.get(key), list):
                    _check_steps(step[key], path, scope, problems)
        finally:
            scope.pop()
        _record_names(step, spec, scope)


def _check_structure(step: dict[str, Any], spec: OpSpec, path: str, problems: list[Problem]) -> None:
    schema = dict(_schema()["$defs"][f"op_{spec.name}"])
    schema["$defs"] = _schema()["$defs"]
    validator = jsonschema.Draft202012Validator(schema)
    seen: set[str] = set()
    for err in sorted(validator.iter_errors(step), key=lambda e: (len(e.path), list(map(str, e.path)))):
        if err.path and err.path[0] == "steps" and len(err.path) > 1:
            continue  # nested steps are validated one by one with their own paths
        message = _explain(err, spec)
        if message and message not in seen:
            seen.add(message)
            problems.append(Problem(path, message))


def _explain(err: jsonschema.ValidationError, spec: OpSpec) -> str | None:
    """Turn a jsonschema error into a short, specific message."""
    path = list(err.path)
    if err.validator == "required" and not path:
        name = err.message.split("'")[1]
        param = spec.param(name)
        what = f" ({param.help})" if param and param.help and param.help != name else ""
        return f"missing required parameter '{name}'{what}"
    if err.validator == "additionalProperties" and not path:
        known = [p.name for p in spec.params] + [a for p in spec.params for a in p.aliases] + list(COMMON_KEYS)
        return "; ".join(f"unknown parameter '{k}'{_did_you_mean(k, known)}" for k in _extra_keys(err))
    if err.validator == "anyOf" and not path:
        groups = [opt.get("required", ["?"])[0] for sub in [err.schema] for opt in _flatten_any_of(sub)]
        return "needs one of: " + ", ".join(f"'{g}'" for g in dict.fromkeys(groups))
    if not path:
        return _short(err.message)
    key = str(path[0])
    if key == "repeat":
        if err.validator == "additionalProperties":
            keys = ["count", "offset", "rotate", "var", "values", "as"]
            return "; ".join(f"repeat: unknown key '{k}'{_did_you_mean(k, keys)}" for k in _extra_keys(err))
        return f"repeat: {_short(err.message)} (use {{\"count\": n, \"offset\": [dx, dy, dz]}})"
    param = spec.param(key)
    if param is None:
        return f"'{key}': {_short(err.message)}"
    expected = PARAM_TYPES.get(param.type, param.type)
    if param.type == "enum":
        expected = "one of " + ", ".join(param.choices or ())
    return f"parameter '{key}': expected {expected}; got {_preview(_get(err))}"


def _flatten_any_of(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("anyOf", []))


def _extra_keys(err: jsonschema.ValidationError) -> list[str]:
    allowed = set(err.schema.get("properties", {}))
    return [k for k in err.instance if k not in allowed]


# ---------------------------------------------------------------------- meaning


class _Scope:
    """Names visible to a step: variables, references and components."""

    def __init__(self, variables: set[str]) -> None:
        self.variables = set(variables)
        self.loop: list[set[str]] = []
        self.refs: set[str] = set()
        self.components: set[str] = set()

    def push(self, names: set[str]) -> None:
        self.loop.append(names)

    def pop(self) -> None:
        self.loop.pop()

    def all_variables(self) -> set[str]:
        out = set(self.variables)
        for names in self.loop:
            out |= names
        return out


def _loop_variables(rep: Any) -> set[str]:
    if not isinstance(rep, dict):
        return set()
    names = {str(rep.get("var", "i")), "count"}
    if "values" in rep:
        names.add(str(rep.get("as", "value")))
    return names


def _check_variables(variables: Any, scope: _Scope, problems: list[Problem]) -> None:
    if not isinstance(variables, dict):
        return
    defined: set[str] = set()
    for name, raw in variables.items():
        for text in _strings(raw):
            for message in _expression_problems(text, defined, scope.refs, allow_queries=False):
                problems.append(Problem(f"variables.{name}", message))
        defined.add(name)


def _check_meaning(step: dict[str, Any], spec: OpSpec, path: str, scope: _Scope, problems: list[Problem]) -> None:
    variables = scope.all_variables()
    for key, raw in step.items():
        if key in ("op", "id", "comment", "steps", "material", "tag"):
            continue
        if key == "variables":
            for name, value in (raw or {}).items():
                for text in _strings(value):
                    problems.extend(Problem(path, f"variable '{name}': {m}")
                                    for m in _expression_problems(text, variables, scope.refs))
                scope.variables.add(name)
            continue
        if key == "in":
            _check_reference(raw, path, "in", scope, problems)
            continue
        if key == "repeat":
            raw = {k: v for k, v in (raw or {}).items() if k not in ("var", "as")} if isinstance(raw, dict) else raw
        param = spec.param(key)
        if param is None and key not in ("if", "repeat"):
            continue
        kind = param.type if param else "value"
        if kind in ("target", "path") and (isinstance(raw, str) or (isinstance(raw, list) and raw and isinstance(raw[0], str))):
            for name in ([raw] if isinstance(raw, str) else raw):
                _check_reference(name, path, key, scope, problems)
            continue
        if kind == "component":
            if raw not in scope.components:
                known = sorted(scope.components)
                problems.append(Problem(path, f"unknown component '{raw}'{_did_you_mean(str(raw), known)}"
                                        + ("" if known else " (define it first with the 'component' op)")))
            continue
        if kind in _NUMERIC or kind in _POINTY or kind in ("value", "points", "loops", "rotation", "depth", "face"):
            for text in _strings(raw, skip_words=kind in ("direction", "anchor", "depth", "face")):
                for message in _expression_problems(text, variables, scope.refs):
                    problems.append(Problem(path, f"'{key}': {message}"))


def _check_reference(raw: Any, path: str, key: str, scope: _Scope, problems: list[Problem]) -> None:
    name = str(raw).split("[", 1)[0].strip()
    if name == "*" or name in scope.refs or name in scope.components:
        return
    known = sorted(scope.refs | scope.components)
    extra = _did_you_mean(name, known) or (" (no earlier step has an id)" if not known else "")
    problems.append(Problem(path, f"'{key}' refers to unknown id '{name}'{extra}"))


def _record_names(step: dict[str, Any], spec: OpSpec, scope: _Scope) -> None:
    step_id = step.get("id")
    if spec.name in ("component", "make_component"):
        key = step_id or step.get("name")
        if key:
            scope.components.add(str(key))
        if spec.name == "component":
            return
    if step_id:
        scope.refs.add(str(step_id))
    if spec.name in ("group", "make_group") and step.get("name"):
        scope.refs.add(str(step["name"]))


def _expression_problems(text: str, variables: Iterable[str], refs: set[str], allow_queries: bool = True) -> list[str]:
    try:
        node = parse(text)
    except ExprError as exc:
        return [str(exc)]
    out = []
    known = set(variables)
    for name in variables_used(node):
        if name not in known:
            hint = _did_you_mean("$" + name, ["$" + k for k in known])
            out.append(f"unknown variable ${name}{hint or (' (not defined in variables)' if known else '')}")
    for name in queries_used(node):
        if not allow_queries:
            out.append(f"@{name}: geometry queries are not available in top-level variables (use a 'set' step)")
        elif name not in refs:
            out.append(f"@{name} refers to unknown id '{name}'{_did_you_mean(name, sorted(refs))}")
    return out


def _strings(raw: Any, skip_words: bool = False) -> list[str]:
    """Expression strings inside a value (recursing into lists and objects)."""
    if isinstance(raw, str):
        word = raw.strip().lower()
        if skip_words and (word.isalpha() or word.lstrip("+-") in ("x", "y", "z")):
            return []
        return [raw]
    if isinstance(raw, list):
        return [s for item in raw for s in _strings(item, skip_words)]
    if isinstance(raw, dict):
        return [s for k, item in raw.items() for s in _strings(item, skip_words or k == "axis")]
    return []


# ---------------------------------------------------------------------- helpers


def _did_you_mean(word: str, options: Iterable[str]) -> str:
    hint = difflib.get_close_matches(word, list(options), 1, 0.6)
    return f" (did you mean '{hint[0]}'?)" if hint else ""


def _join(prefix: str, label: str) -> str:
    return f"{prefix} > {label}" if prefix else label


def _short(message: str) -> str:
    return message if len(message) < 160 else message[:157] + "..."


def _preview(value: Any) -> str:
    import json

    text = json.dumps(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _get(err: jsonschema.ValidationError) -> Any:
    return err.instance
