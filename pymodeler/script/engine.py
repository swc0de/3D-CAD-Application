"""The build-script engine: runs a validated script's steps against a model.

Every step calls a registered op (the same ones the GUI uses).  The engine adds the
script-level conveniences: units and variables, ``repeat`` loops with a moving
coordinate frame, ``if`` conditions, ``in`` (edit inside a group), common ``material``
and ``tag`` keys, and *named references*: a step's ``id`` names what it produced, and
the change log keeps every name pointing at the right geometry as faces split, merge
or move into groups.
"""

from __future__ import annotations

import copy
import difflib
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from pymodeler.core.changes import ChangeLog
from pymodeler.core.components import ComponentDefinition, ComponentInstance
from pymodeler.core.entities import Entities, Entity
from pymodeler.core.materials import ColorError, Material, parse_color
from pymodeler.core.model import Model
from pymodeler.core.tags import Tag
from pymodeler.core.transform import apply_point, apply_vector, rotation, translation
from pymodeler.core.units import MM_PER_UNIT, UnitError, normalize_unit
from pymodeler.core.vec import GeometryError
from pymodeler.ops.library import target_bounds
from pymodeler.ops.organize import paint, set_tag
from pymodeler.ops.registry import OpContext, OpOutput, OpSpec, Target, get_op
from pymodeler.script.errors import ScriptError, ScriptValidationError
from pymodeler.script.expr import Env, ExprError, evaluate
from pymodeler.script.values import ValueReader

COMMON_KEYS = ("op", "id", "comment", "repeat", "if", "material", "tag", "in")
"""Keys every step may use in addition to its op's parameters."""


@dataclass
class StepRecord:
    """Summary of one executed step (for reports)."""

    path: str
    op: str
    created: int


@dataclass
class BuildResult:
    """A built model plus everything the script named."""

    model: Model
    refs: dict[str, list[list[Entity]]] = field(default_factory=dict)
    components: dict[str, ComponentDefinition] = field(default_factory=dict)
    records: list[StepRecord] = field(default_factory=list)
    script: dict[str, Any] = field(default_factory=dict)


class Engine:
    """Runs one build script."""

    def __init__(self, script: dict[str, Any], model: Model | None = None) -> None:
        self.script = script
        try:
            units = normalize_unit(str(script.get("units", "mm")))
        except UnitError as exc:
            raise ScriptError(str(exc), "units") from None
        self.model = model if model is not None else Model(units=units)
        self.model.units = units
        self.mm = MM_PER_UNIT[units]
        self.scopes: list[dict[str, Any]] = [{}]
        self.refs: dict[str, list[list[int]]] = {}
        self.components: dict[str, ComponentDefinition] = {}
        self.records: list[StepRecord] = []
        self.current_id: str | None = None
        self._paths: list[str] = []

    # ------------------------------------------------------------------ public API
    def build(self) -> BuildResult:
        """Run the whole script.

        Raises:
            ScriptError: with the failing step's path.
        """
        self.model.name = str(self.script.get("name", ""))
        self.model.description = str(self.script.get("description", ""))
        self._load_materials()
        self._load_tags()
        self.set_variables(self.script.get("variables", {}), where="variables")
        self._run_steps(self.script.get("steps", []), self.model.entities, np.eye(4), "")
        return BuildResult(
            model=self.model,
            refs={name: self._parts(name) for name in self.refs},
            components=dict(self.components),
            records=self.records,
            script=self.script,
        )

    def run_nested(self, steps: list[dict[str, Any]], entities: Entities, frame: np.ndarray) -> None:
        """Run nested steps (used by the ``group`` and ``component`` ops)."""
        saved = self.current_id
        self._run_steps(steps, entities, frame, self._paths[-1] if self._paths else "")
        self.current_id = saved

    def register_component(self, key: str, definition: ComponentDefinition) -> None:
        """Make a component available to ``place`` under ``key``."""
        self.components[key] = definition

    def set_variables(self, mapping: Any, where: str | None = None) -> None:
        """Evaluate and assign variables in order (later ones may use earlier ones)."""
        if not isinstance(mapping, dict):
            raise ScriptError("variables must be an object of names to values", where or self._path())
        scope = self.scopes[0]
        for name, raw in mapping.items():
            try:
                scope[name] = self._variable_value(raw)
            except (ExprError, ValueError) as exc:
                raise ScriptError(f"variable {name!r}: {exc}", where or self._path()) from None

    # ------------------------------------------------------------------ setup
    def _load_materials(self) -> None:
        for name, spec in (self.script.get("materials") or {}).items():
            try:
                self.model.add_material(Material.from_spec(name, spec))
            except ColorError as exc:
                raise ScriptError(str(exc), f"materials.{name}") from None

    def _load_tags(self) -> None:
        for name, spec in (self.script.get("tags") or {}).items():
            try:
                self.model.tags[name] = Tag.from_spec(name, spec)
            except (ValueError, ColorError) as exc:
                raise ScriptError(str(exc), f"tags.{name}") from None

    def _variable_value(self, raw: Any) -> Any:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            return evaluate(raw, self._env())
        if isinstance(raw, list):
            return [self._variable_value(x) for x in raw]
        raise ExprError(f"unsupported value {raw!r}")

    # ------------------------------------------------------------------ steps
    def _run_steps(self, steps: list[dict[str, Any]], entities: Entities, frame: np.ndarray, prefix: str) -> None:
        for index, step in enumerate(steps, 1):
            label = f"step {index} ({step.get('op', '?')}"
            if step.get("id"):
                label += f" '{step['id']}'"
            path = f"{prefix} > {label})" if prefix else f"{label})"
            self._paths.append(path)
            try:
                self._run_step(step, entities, frame, path)
            finally:
                self._paths.pop()

    def _run_step(self, step: dict[str, Any], entities: Entities, frame: np.ndarray, path: str) -> None:
        spec = get_op(str(step.get("op")))
        if spec is None:
            raise ScriptError(f"unknown op {step.get('op')!r}", path)
        step_id = step.get("id")
        self.current_id = step_id
        collected: list[list[Entity]] = []
        for k, (bindings, iter_frame) in enumerate(self._iterations(step, frame, path)):
            where = path if "repeat" not in step else f"{path}, iteration {k + 1}"
            self.scopes.append(bindings)
            try:
                if "if" in step and not self._condition(step["if"], where):
                    continue
                out = self._execute(step, spec, entities, iter_frame, where)
            finally:
                self.scopes.pop()
            collected.extend(out.parts)
        if step_id and spec.name not in ("component", "set"):
            self.refs[str(step_id)] = [[e.id for e in part] for part in collected]

    def _iterations(self, step: dict[str, Any], frame: np.ndarray, path: str) -> list[tuple[dict[str, Any], np.ndarray]]:
        """Variable bindings and frames for each repetition of a step."""
        rep = step.get("repeat")
        if rep is None:
            return [({}, frame)]
        try:
            values = rep.get("values")
            count = len(values) if values is not None else self._reader().integer(rep.get("count", 1))
            if count < 0 or count > 10000:
                raise ExprError("repeat count must be between 0 and 10000")
            offset = self._reader().point(rep["offset"]) if rep.get("offset") is not None else None
            rot = rep.get("rotate")
            if rot is not None:
                axis = self._reader().direction(rot.get("axis", "z"))
                angle = self._reader().number(rot.get("angle", 0))
                centre = self._reader().point(rot.get("center", [0, 0, 0]))
        except (ExprError, ValueError, KeyError, TypeError) as exc:
            raise ScriptError(f"repeat: {exc}", path) from None
        out = []
        index_var = str(rep.get("var", "i"))
        for k in range(count):
            bindings: dict[str, Any] = {index_var: float(k), "count": float(count)}
            if values is not None:
                bindings[str(rep.get("as", "value"))] = self._variable_value(values[k])
            m = frame
            if offset is not None:
                m = m @ translation(offset * k)
            if rot is not None:
                m = m @ rotation(axis, angle * k, centre)
            out.append((bindings, m))
        return out

    def _condition(self, raw: Any, path: str) -> bool:
        try:
            return self._reader().boolean(raw)
        except (ExprError, ValueError) as exc:
            raise ScriptError(f"if: {exc}", path) from None

    def _execute(self, step: dict[str, Any], spec: OpSpec, entities: Entities, frame: np.ndarray, path: str) -> OpOutput:
        """Convert arguments, run the op, then update references."""
        registry = self.model.registry
        log = registry.begin()
        try:
            active = self._active_entities(step, entities, path)
            args, origins = self._arguments(spec, step, frame, path)
            ctx = OpContext(self.model, active, frame, engine=self)
            out = spec.run(ctx, args)
        except ScriptError:
            raise
        except (GeometryError, ExprError, ValueError, KeyError, TypeError) as exc:
            raise ScriptError(_clean(exc), path) from None
        except Exception as exc:  # noqa: BLE001 - never crash on a bad script
            detail = traceback.format_exc(limit=3)
            raise ScriptError(f"internal error: {exc!r}\n{detail}", path) from None
        finally:
            registry.end(log)
        self._update_refs(log)
        if spec.grows_target:
            self._grow(origins, out)
        self._apply_common(step, spec, out, path)
        for name, parts in out.names.items():
            if name and name not in self.refs:
                self.refs[name] = [[e.id for e in part] for part in parts]
        created = sum(len(p) for p in out.parts)
        self.records.append(StepRecord(path, spec.name, created))
        return out

    def _active_entities(self, step: dict[str, Any], entities: Entities, path: str) -> Entities:
        """The collection a step draws into (``in`` switches to a group's contents)."""
        if "in" not in step:
            return entities
        target, _ = self._resolve_target(step["in"])
        for e in target.all():
            if isinstance(e, ComponentInstance):
                return e.definition.entities
        raise ScriptError(f"'in': {step['in']!r} is not a group or component", path)

    def _arguments(
        self, spec: OpSpec, step: dict[str, Any], frame: np.ndarray, path: str
    ) -> tuple[dict[str, Any], list[tuple[str, int] | None]]:
        """Typed arguments for an op (defaults filled in) plus target part origins."""
        reader = self._reader()
        args: dict[str, Any] = {}
        origins: list[tuple[str, int] | None] = []
        for param in spec.params:
            raw = None
            for key in (param.name, *param.aliases):
                if key in step:
                    raw = step[key]
                    break
            if raw is None and param.name not in step:
                raw = copy.deepcopy(param.default)
            try:
                if param.type == "target" and raw is not None and param.name == "target":
                    target, origins = self._resolve_target(raw)
                    args[param.name] = target
                    continue
                value = reader.read(param, raw)
            except (ExprError, ValueError, GeometryError) as exc:
                raise ScriptError(f"parameter '{param.name}': {_clean(exc)}", path) from None
            if param.type == "face" and isinstance(value, dict):
                value = self._face_to_world(value, frame)
            if param.required and value is None:
                raise ScriptError(f"missing required parameter '{param.name}'", path)
            args[param.name] = value
        return args, origins

    @staticmethod
    def _face_to_world(selector: dict[str, Any], frame: np.ndarray) -> dict[str, Any]:
        if "near" in selector:
            return {"near": apply_point(frame, selector["near"])}
        if "normal" in selector:
            return {"normal": apply_vector(frame, selector["normal"])}
        return selector

    # ------------------------------------------------------------------ references
    def _parts(self, name: str) -> list[list[Entity]]:
        """Live entities of a reference, part by part."""
        out = []
        for part in self.refs[name]:
            ents = [self.model.registry.get(i) for i in part]
            out.append([e for e in ents if isinstance(e, Entity) and e.alive])
        return out

    def _resolve_target(self, raw: Any) -> tuple[Target, list[tuple[str, int] | None]]:
        """Turn a reference (name, ``name[i]``, list of names or ``*``) into a target."""
        names = raw if isinstance(raw, list) else [raw]
        parts: list[list[Entity]] = []
        origins: list[tuple[str, int] | None] = []
        for item in names:
            name, index = _split_index(str(item))
            if name == "*":
                ents = self.model.entities
                parts.append([*ents.faces.values(), *ents.edges.values(), *ents.instances.values()])
                origins.append(None)
                continue
            if name in self.refs:
                ref_parts = self._parts(name)
                chosen = range(len(ref_parts)) if index is None else [self._check_index(name, index, len(ref_parts))]
                for i in chosen:
                    parts.append(ref_parts[i])
                    origins.append((name, i))
                continue
            if name in self.components:
                instances = [i for i in self.components[name].instances if i.alive]
                parts.append(list(instances))
                origins.append(None)
                continue
            known = list(self.refs) + list(self.components)
            hint = difflib.get_close_matches(name, known, 1)
            extra = f" (did you mean '{hint[0]}'?)" if hint else (
                f" (known: {', '.join(known[:12])})" if known else " (no step has an id yet)")
            raise ValueError(f"unknown reference '{name}'{extra}")
        if not any(parts):
            raise ValueError(f"reference {raw!r} no longer refers to any geometry (was it erased or exploded?)")
        return Target(name=",".join(str(n) for n in names), parts=parts), origins

    @staticmethod
    def _check_index(name: str, index: int, count: int) -> int:
        if not -count <= index < count:
            raise ValueError(f"'{name}[{index}]' is out of range ('{name}' has {count} part(s))")
        return index % count

    def _resolve_component(self, name: str) -> ComponentDefinition:
        if name in self.components:
            return self.components[name]
        if name in self.model.definitions and not self.model.definitions[name].is_group:
            return self.model.definitions[name]
        hint = difflib.get_close_matches(name, list(self.components), 1)
        extra = f" (did you mean '{hint[0]}'?)" if hint else " (define it first with the 'component' op)"
        raise ValueError(f"unknown component '{name}'{extra}")

    def _resolve_material(self, name: str | None) -> str | None:
        if name is None:
            return None
        if name in self.model.materials:
            return name
        try:
            color, alpha = parse_color(name)
        except ColorError:
            hint = difflib.get_close_matches(name, list(self.model.materials), 1)
            extra = f" (did you mean '{hint[0]}'?)" if hint else " (declare it under \"materials\")"
            raise ValueError(f"unknown material '{name}'{extra}") from None
        self.model.add_material(Material(name, color, alpha if alpha is not None else 1.0))
        return name

    def _update_refs(self, log: ChangeLog) -> None:
        """Re-point every reference after an operation split, merged or moved entities."""
        if not (log.replaced or log.erased):
            return
        registry = self.model.registry
        for name, parts in self.refs.items():
            new_parts = []
            for part in parts:
                ids: dict[int, None] = {}
                for i in part:
                    for j in log.successors(i, registry):
                        ids[j] = None
                new_parts.append(list(ids))
            self.refs[name] = new_parts

    def _grow(self, origins: list[tuple[str, int] | None], out: OpOutput) -> None:
        """Add an op's new entities to the references they came from."""
        for origin, part in zip(origins, out.parts, strict=False):
            if origin is None:
                continue
            name, index = origin
            existing = self.refs[name][index]
            existing.extend(e.id for e in part if e.id not in existing)

    def _apply_common(self, step: dict[str, Any], spec: OpSpec, out: OpOutput, path: str) -> None:
        """Apply a step's ``material`` and ``tag`` keys to what it created."""
        items = [e for part in out.parts for e in part if e.alive]
        try:
            if "material" in step and spec.param("material") is None:
                paint(self.model, items, self._resolve_material(step["material"]), "both")
            if "tag" in step and spec.param("tag") is None:
                set_tag(self.model, items, str(step["tag"]))
        except (GeometryError, ValueError) as exc:
            raise ScriptError(_clean(exc), path) from None

    # ------------------------------------------------------------------ expressions
    def _env(self) -> Env:
        return Env(
            variable=self._lookup,
            query=self._query,
            mm_per_unit=self.mm,
            known_variables=lambda: sorted({k for scope in self.scopes for k in scope}),
        )

    def _reader(self) -> ValueReader:
        return ValueReader(
            self._env(),
            self.mm,
            lambda raw: self._resolve_target(raw)[0],
            self._resolve_component,
            self._resolve_material,
        )

    def _lookup(self, name: str) -> Any:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise KeyError(name)

    def _query(self, name: str, index: int | None, prop: str) -> float:
        """``@name.prop``: bounding-box values of a reference, in file units."""
        if name in self.refs:
            parts = self._parts(name)
            if index is not None:
                parts = [parts[self._check_index(name, index, len(parts))]]
            box = target_bounds([e for part in parts for e in part])
        elif name in self.components:
            box = self.components[name].local_bounds()
        else:
            hint = difflib.get_close_matches(name, list(self.refs), 1)
            extra = f" (did you mean '@{hint[0]}'?)" if hint else ""
            raise ExprError(f"unknown reference '@{name}'{extra}")
        if box is None:
            raise ExprError(f"'@{name}' has no geometry")
        lo, hi = box
        axis = "xyz".index(prop[0]) if prop[0] in "xyz" else None
        if prop in ("width", "depth", "height"):
            axis = ("width", "depth", "height").index(prop)
            value = hi[axis] - lo[axis]
        elif prop.endswith("min"):
            value = lo[axis]
        elif prop.endswith("max"):
            value = hi[axis]
        else:
            value = (lo[axis] + hi[axis]) / 2
        return float(value) / self.mm

    def _path(self) -> str:
        return self._paths[-1] if self._paths else ""


def _split_index(name: str) -> tuple[str, int | None]:
    """``"post[2]"`` -> ``("post", 2)``."""
    name = name.strip()
    if name.endswith("]") and "[" in name:
        base, _, rest = name.partition("[")
        try:
            return base.strip(), int(rest[:-1])
        except ValueError:
            raise ValueError(f"bad index in reference {name!r}") from None
    return name, None


def _clean(exc: BaseException) -> str:
    """Exception text without KeyError's quotes."""
    text = str(exc)
    if isinstance(exc, KeyError) and text.startswith("'") and text.endswith("'"):
        return f"missing {text}"
    return text


def build_script(script: dict[str, Any], validate: bool = True) -> BuildResult:
    """Validate and run a build script.

    Raises:
        ScriptValidationError: listing every validation problem.
        ScriptError: if a step fails while building.
    """
    if validate:
        from pymodeler.script.validate import validate_script

        problems = validate_script(script)
        if problems:
            raise ScriptValidationError(problems)
    return Engine(script).build()


def load_script(path: str | Path) -> dict[str, Any]:
    """Read a build script file.

    Raises:
        ScriptError: if the file is missing or not valid JSON.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(f"cannot read {path}: {exc.strerror or exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScriptError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}", str(path)) from None
    if not isinstance(data, dict):
        raise ScriptError("a build script must be a JSON object with \"version\" and \"steps\"", str(path))
    return data


def build_file(path: str | Path, validate: bool = True) -> BuildResult:
    """Load and build a script file."""
    return build_script(load_script(path), validate=validate)
