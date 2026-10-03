"""Generate ``docs/BUILD_SCRIPT_REFERENCE.md`` from the op registry and schema."""

from __future__ import annotations

import json
from pathlib import Path

from pymodeler.ops.registry import ALIASES, PARAM_TYPES, OpSpec, Param, all_ops
from pymodeler.ops.selectors import DIRECTIONS
from pymodeler.script.expr import FUNCTIONS, QUERY_PROPS

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "BUILD_SCRIPT_REFERENCE.md"

CATEGORIES = [
    ("draw", "Drawing"),
    ("primitive", "Primitive solids"),
    ("modify", "Modifying faces"),
    ("transform", "Moving and copying"),
    ("organize", "Groups, components, materials and variables"),
    ("edit", "Erasing and intersecting"),
]

INTRO = """# Build script reference

> Generated from the op registry by `python -m pymodeler docs`. Do not edit by hand.

A build script is a JSON object describing a model as an ordered list of steps. Every
step calls one operation; the same operations power the app's tools. See
[CLAUDE.md](../CLAUDE.md) for the workflow and modelling tips. The JSON Schema is in
[schema/build_script.schema.json](../schema/build_script.schema.json).

## File structure

```json
{
  "version": 1,
  "units": "mm",
  "name": "Garden shed",
  "variables": { "w": 3000, "d": 2000, "h": "2.2m" },
  "materials": { "wood": "#b07d4f", "glass": { "color": "#88ccff", "opacity": 0.4 } },
  "tags": { "Roof": { "visible": true } },
  "steps": [ { "op": "box", "id": "base", "size": ["$w", "$d", 100] } ]
}
```

| Key | Meaning |
| --- | --- |
| `version` | Always `1`. |
| `units` | Units of bare numbers: `mm` (default), `cm`, `m`, `in` or `ft`. |
| `variables` | Named values used as `$name`; later ones may use earlier ones. |
| `materials` | Name to colour (`"#a0522d"`, a colour name, `[r, g, b]`) or `{"color", "opacity"}`. |
| `tags` | Tag (layer) names with optional `visible` and `color`. |
| `steps` | The operations, run in order. |

## Coordinates

Z is up, like SketchUp: **X = red** (right), **Y = green** (away from you), **Z = blue** (up).
The *front* view looks along +Y, so the front of a building faces **-Y**. Angles are degrees,
counter-clockwise when looking down the rotation axis.

## Values

Anywhere a number is expected you may write:

* a number in the file's units: `2400`
* a number with a unit: `"2.4m"`, `"300mm"`, `"8ft"`, `"6in"`, `"8' 6\""`, `"45deg"`, `"0.5rad"`
* an expression: `"$w / 2 - $wall"`, `"max($a, 300mm)"`, `"@table.zmax + 10"`

Expressions support `+ - * / // % ^`, comparisons (`< <= > >= == !=`), `and`, `or`, `not`,
the constants `pi`, `e`, `true`, `false`, and these functions (trigonometry in degrees):
@@FUNCTIONS@@.

**Geometry queries** `@id.property` read the world bounding box of an earlier step's result,
in file units: @@QUERIES@@. Use `@id[2].zmax` for one part of a repeated step.

Points are `[x, y, z]` (z may be omitted). Directions are `"x"`, `"-y"`, `"z"`, `"up"` ... or a vector.

## Common step keys

| Key | Meaning |
| --- | --- |
| `op` | The operation (see below). |
| `id` | Name for what the step creates; later steps use it as `target`, in `@id.zmax`, or `id[n]`. |
| `comment` | Free text, ignored. |
| `material` | Paint whatever the step creates (both sides). |
| `tag` | Put whatever the step creates on a tag. |
| `if` | Expression; the step is skipped when it is false/0. |
| `in` | Run the step inside an existing group or component (by id or name). |
| `repeat` | Run the step several times (see below). |

### References

A step's `id` names the **set of entities it produced**. Operations that change geometry keep
names up to date: after `push_pull`, the rectangle's id refers to the whole box; after
`make_group`, it refers to the group. A repeated step's id has one *part* per iteration
(`posts[0]`, `posts[1]`, ...). `"*"` targets everything at the top level.

### Repeat

```json
{ "op": "place", "component": "post", "position": [0, 0, 0],
  "repeat": { "count": 6, "offset": [1200, 0, 0] } }
{ "op": "box", "origin": [0, "$i * 280", "$i * 180"], "size": [1000, 280, 180],
  "repeat": { "count": 12 } }
{ "op": "box", "origin": [3000, 0, 0], "size": [400, 200, 900],
  "repeat": { "count": 8, "rotate": { "angle": 45, "axis": "z", "center": [0, 0, 0] } } }
{ "op": "cylinder", "center": "$value", "radius": 40, "height": 700,
  "repeat": { "values": [[0, 0], [1500, 0], [0, 800], [1500, 800]] } }
```

`count` repetitions get `$i` = 0, 1, 2, ... (rename with `"var"`) and `$count`. `offset` moves
each repetition by `i x offset`; `rotate` turns it by `i x angle`. With `values`, the current
value is `$value` (rename with `"as"`).

## Face selectors

Ops that work on a face take a `face` selector. Direction words pick the faces pointing that way
that lie furthest along it (so `"top"` is a box's lid): @@DIRECTIONS@@. Also `"all"`, `"largest"`,
`"smallest"`, `{"normal": [x, y, z]}`, `{"near": [x, y, z]}` and `{"index": n}`. If the target is
a single face (just drawn), `face` can be omitted. Selecting the back of a lone face (e.g.
`"bottom"` of a rectangle drawn on the ground) is allowed and works on its other side.
"""


def _type_text(p: Param) -> str:
    if p.type == "enum":
        return " \\| ".join(f"`{c}`" for c in (p.choices or ()))
    return p.type


def _default_text(p: Param) -> str:
    if p.required:
        return "**required**"
    if p.default is None:
        return ""
    return f"`{json.dumps(p.default)}`"


def op_section(spec: OpSpec) -> str:
    """Markdown for one op."""
    aliases = [a for a, t in ALIASES.items() if t == spec.name]
    lines = [f"### `{spec.name}`", "", spec.summary]
    if aliases:
        lines[-1] += f" Alias: {', '.join(f'`{a}`' for a in aliases)}."
    if spec.notes:
        lines += ["", spec.notes]
    lines += ["", "| Parameter | Type | Default | Description |", "| --- | --- | --- | --- |"]
    for p in spec.params:
        name = f"`{p.name}`" + "".join(f" / `{a}`" for a in p.aliases)
        lines.append(f"| {name} | {_type_text(p)} | {_default_text(p)} | {p.help} |")
    for group in spec.one_of:
        lines.append("")
        lines.append("Requires one of: " + ", ".join(f"`{g}`" for g in group) + ".")
    if spec.grows_target:
        lines += ["", "New faces are added to the target's reference."]
    if spec.example:
        lines += ["", "```json", json.dumps(spec.example), "```"]
    return "\n".join(lines)


def generate_docs() -> str:
    """The complete reference document."""
    ops = all_ops()
    intro = (
        INTRO.replace("@@FUNCTIONS@@", ", ".join(f"`{f}`" for f in FUNCTIONS))
        .replace("@@QUERIES@@", ", ".join(f"`{q}`" for q in QUERY_PROPS))
        .replace("@@DIRECTIONS@@", ", ".join(f"`{d}`" for d in DIRECTIONS))
    )
    parts = [intro.rstrip(), "", "## Operations", ""]
    for key, title in CATEGORIES:
        specs = [s for s in ops if s.category == key]
        if not specs:
            continue
        parts.append("- **" + title + "**: " + ", ".join(f"[`{s.name}`](#{s.name})" for s in specs))
    parts.append("")
    for key, title in CATEGORIES:
        specs = [s for s in ops if s.category == key]
        if not specs:
            continue
        parts += [f"## {title}", ""]
        for spec in specs:
            parts += [op_section(spec), ""]
    parts += ["## Parameter types", "", "| Type | Accepts |", "| --- | --- |"]
    parts += [f"| {k} | {v} |" for k, v in PARAM_TYPES.items()]
    return "\n".join(parts).rstrip() + "\n"


def write_docs(path: Path = DOCS_PATH) -> Path:
    """Write the reference document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_docs(), encoding="utf-8")
    return path



CLAUDE_PATH = Path(__file__).resolve().parents[2] / "CLAUDE.md"
OPS_BEGIN = "<!-- BEGIN GENERATED OPS (python -m pymodeler docs) -->"
OPS_END = "<!-- END GENERATED OPS -->"


def ops_summary() -> str:
    """Compact list of every op and its parameters (embedded in CLAUDE.md)."""
    lines = []
    for key, title in CATEGORIES:
        specs = [s for s in all_ops() if s.category == key]
        if not specs:
            continue
        lines.append(f"**{title}**")
        lines.append("")
        for spec in specs:
            params = ", ".join(
                f"`{p.name}`" + "".join(f"/`{a}`" for a in p.aliases) + ("*" if p.required else "")
                + (f" (={json.dumps(p.default)})" if p.default not in (None, False, "") and not p.required else "")
                for p in spec.params
            )
            groups = "".join(f"; needs one of {'/'.join(g)}" for g in spec.one_of)
            lines.append(f"- `{spec.name}`: {spec.summary} Params: {params}{groups}.")
        lines.append("")
    lines.append("`*` = required. Every step also accepts `id`, `comment`, `material`, `tag`, `if`, `in`, `repeat`.")
    return "\n".join(lines)


def updated_claude_md(text: str) -> str:
    """CLAUDE.md text with the generated ops section refreshed."""
    if OPS_BEGIN not in text or OPS_END not in text:
        return text
    head, rest = text.split(OPS_BEGIN, 1)
    _, tail = rest.split(OPS_END, 1)
    return f"{head}{OPS_BEGIN}\n{ops_summary()}\n{OPS_END}{tail}"


def write_claude_md(path: Path = CLAUDE_PATH) -> Path:
    """Refresh the generated section of CLAUDE.md."""
    path.write_text(updated_claude_md(path.read_text(encoding="utf-8")), encoding="utf-8")
    return path
