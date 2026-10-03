# CLAUDE.md

Guidance for Claude (and other AI assistants) working in this repository: writing
**build scripts** that create 3D models, and developing PyModeler itself.

PyModeler is a SketchUp-style 3D modeler in pure Python. Models can be built from JSON
build scripts: an ordered list of modeling steps that call the same operations as the
app's tools.

## The modelling workflow

```bash
# 1. write or edit a script, e.g. scripts/shed.json (start from a file in examples/)
# 2. check it (fast, no geometry) and fix every problem reported
python -m pymodeler validate scripts/shed.json
# 3. build it with previews and a size report
python -m pymodeler build scripts/shed.json -o out/shed.pym --preview out/shed.png --report
# 4. LOOK at out/shed.png (a 2x2 contact sheet: iso, front, top, right) and read the report
# 5. fix what is wrong, then repeat from step 2
```

* `out/shed.png` shows all four views with the overall size (W x D x H) and the ground-grid
  spacing in the header. `out/shed_iso.png` etc. are the single views (pass
  `--views iso,front,back,left,right,top,bottom` and `--size 1200x900` to change them).
* `--report` prints every `id`'s bounding box (`min`, `max`, `size`) in the file's units and
  whether it is a closed `solid`. Use it to check positions and sizes exactly.
* Errors always name the step: `step 4 (push_pull 'roof'): ...`. Nested steps read
  `step 2 (group 'table') > step 3 (box 'leg')`, and repeats add `, iteration 3`.
* Export with `--export out/shed.glb` (also `.obj`, `.stl`, `.gltf`). `python -m pymodeler info file`
  summarises an existing `.pym` or script. `python -m pymodeler ops` lists every operation.
* The `.pym` file embeds the script, so a model can always be rebuilt.

Do not stop after the first build: check the preview against the request (proportions,
openings, things floating or overlapping, missing parts), and iterate.

## Build script format

Full, generated reference: [docs/BUILD_SCRIPT_REFERENCE.md](docs/BUILD_SCRIPT_REFERENCE.md).
JSON Schema: [schema/build_script.schema.json](schema/build_script.schema.json).

```json
{
  "version": 1,
  "units": "mm",
  "name": "Garden shed",
  "variables": { "w": 3000, "d": 2000, "h": 2200, "wall": 100 },
  "materials": { "wood": "#b07d4f", "glass": { "color": "#88ccff", "opacity": 0.4 } },
  "steps": [
    { "op": "box", "id": "base", "size": ["$w", "$d", 100], "material": "wood" },
    { "op": "box", "id": "walls", "origin": [0, 0, 100], "size": ["$w", "$d", "$h"] },
    { "op": "opening", "target": "walls", "face": "front", "origin": [1000, 0, 100],
      "width": 900, "height": 2000, "depth": "$wall" }
  ]
}
```

### Coordinates and units

* **Z is up** (not Y). X = red = right, Y = green = away from the viewer, Z = blue = up.
* The **front** of a model faces **-Y** (the front view looks along +Y). "left" is -X, "right" +X,
  "back" +Y, "top" +Z, "bottom" -Z.
* `units` sets the unit of bare numbers: `mm` (default), `cm`, `m`, `in`, `ft`. Any value may carry
  its own unit: `"2.4m"`, `"300mm"`, `"8ft"`, `"6in"`, `"8' 6\""`. Angles are degrees (`"0.5rad"` works).
* Points are `[x, y, z]`; `[x, y]` means z = 0.

### Values, variables and expressions

* Numbers, unit strings or expressions in strings: `"$w / 2 - $wall"`, `"max($a, 300mm)"`,
  `"$i * 280"`, `"if($i % 2 == 0, 100, 200)"`. Variables always need the `$`.
* Functions: `sin cos tan asin acos atan atan2` (degrees), `sqrt abs min max round floor ceil pow
  hypot clamp if int`; constants `pi e true false`; operators `+ - * / // % ^ < <= > >= == != and or not`.
* Variables are evaluated in order (later ones may use earlier ones). A `set` step defines more
  variables mid-script and may use geometry queries: `{"op": "set", "variables": {"top": "@table.zmax"}}`.
* Geometry queries `@id.prop` read an earlier result's world bounding box in file units:
  `xmin xmax ymin ymax zmin zmax xmid ymid zmid width depth height`; `@id[2].zmax` for one part.
  Use them to stack things: `"origin": [0, 0, "@table.zmax"]`.

### Steps, ids and references

* Every step has an `op`. Optional on every step: `id`, `comment`, `material` (paints what the step
  creates, both sides), `tag`, `if` (skip when false), `in` (run inside a group), `repeat`.
* `id` names what the step created. Later steps refer to it as `"target": "id"`. Names follow the
  geometry: after `push_pull`, a rectangle's id means the whole solid; after `make_group`, the group.
  A `group`/`make_group` `name` also works as a reference. `"*"` means everything at the top level.
* A repeated step's id has one part per iteration: `"posts[0]"`, `"posts[3]"`; plain `"posts"` is all.

### Repeat

```json
"repeat": { "count": 5, "offset": [1000, 0, 0] }                    // $i = 0..4, shifted by i*offset
"repeat": { "count": 8, "rotate": { "angle": 45, "axis": "z", "center": [0, 0, 0] } }
"repeat": { "values": [[0, 0], [1500, 0], [0, 800]], "as": "p" }    // $p = each value, $i = index
"repeat": { "count": 12, "var": "k" }                                // index called $k
```

Positions inside a repeated step can use `$i` directly (stairs: `"origin": [0, "$i * 280", "$i * 175"]`)
**or** rely on `offset`/`rotate`, which move the whole step. Do not do both for the same axis.

### Face selectors

`push_pull`, `offset`, `opening`, `follow_me`, `paint`, `erase`, `move`, `rotate`, `scale` take
`"face"`: `"top"`, `"bottom"`, `"front"`, `"back"`, `"left"`, `"right"`, `"+x"`, `"-x"`, `"+y"`, `"-y"`,
`"+z"`, `"-z"`, `"all"`, `"largest"`, `"smallest"`, `{"normal": [x, y, z]}`, `{"near": [x, y, z]}`,
`{"index": n}`. Direction words pick the faces pointing that way that are **furthest** along it, so
`"top"` is a box's lid and `"front"` its outer front face. When several coplanar faces tie (e.g. the
ring and the inner face after an `offset`), use `{"near": [x, y, z]}` with a point inside the one you
want. A just-drawn single face needs no selector.

### Operations

<!-- BEGIN GENERATED OPS (python -m pymodeler docs) -->
**Drawing**

- `line`: Draw connected straight edges. A closed, flat loop becomes a face. Params: `points`, `from`, `to`, `closed`; needs one of points/from.
- `rectangle`: Draw a rectangular face from a corner (or its centre). Params: `origin` (=[0, 0, 0]), `width`*, `depth`/`height`*, `plane` (="xy"), `normal`, `x_axis`, `center`.
- `circle`: Draw a circular face (a polygon with many sides, marked as one curve). Params: `center` (=[0, 0, 0]), `radius`*, `segments` (=24), `plane` (="xy"), `normal`.
- `arc`: Draw an arc of a circle; optionally close it into a face. Params: `center` (=[0, 0, 0]), `radius`*, `start_angle`, `end_angle`*, `segments` (=12), `close` (="none"), `plane` (="xy"), `normal`, `x_axis`.
- `polygon`: Draw a regular polygon face. Params: `center` (=[0, 0, 0]), `radius`*, `sides`*, `inscribed` (=true), `plane` (="xy"), `normal`.
- `face`: Draw a face from an outline of points (any flat polygon, optionally with holes). Params: `points`*, `holes`, `normal`.

**Primitive solids**

- `box`: An axis-aligned box (in its own group by default). Params: `origin` (=[0, 0, 0]), `size`, `width`, `depth`, `height`, `center`, `group` (=true), `name`; needs one of size/width.
- `cylinder`: A cylinder standing on its base centre (in its own group by default). Params: `center` (=[0, 0, 0]), `radius`*, `height`*, `segments` (=24), `axis` (="z"), `group` (=true), `name`.
- `cone`: A cone, frustum (top_radius > 0) or pyramid (segments 4) on its base centre. Params: `center` (=[0, 0, 0]), `radius`*, `height`*, `top_radius`, `segments` (=24), `axis` (="z"), `group` (=true), `name`.
- `sphere`: A sphere (in its own group by default). Params: `center` (=[0, 0, 0]), `radius`*, `segments` (=24), `rings` (=12), `group` (=true), `name`.

**Modifying faces**

- `push_pull`: Extrude a face along its normal (SketchUp's Push/Pull). Params: `target`*, `face`, `distance`*, `create_new`.
- `follow_me`: Sweep a profile face along a path (SketchUp's Follow Me). Params: `target`*, `face`, `path`*, `closed`.
- `offset`: Draw a copy of a face's outline inside (positive) or outside (negative) it. Params: `target`*, `face`, `distance`*.
- `extrude`: Make a prism: a face from an outline, pushed along a direction. Params: `points`*, `height`, `direction`, `group` (=true), `name`; needs one of height/direction.
- `opening`: Cut a rectangular opening (door, window) into a face of a solid or group. Params: `target`*, `face`*, `origin`*, `width`*, `height`*, `depth` (="through").

**Moving and copying**

- `move`: Move the target by a vector, or from one point to another. Params: `target`*, `face`, `by`, `to`, `from`; needs one of by/to.
- `rotate`: Rotate the target about an axis. Params: `target`*, `face`, `angle`*, `axis` (="z"), `center` (="center").
- `scale`: Scale the target uniformly or per axis. Params: `target`*, `face`, `factor`*, `center` (="center").
- `mirror`: Mirror the target across a plane (optionally keeping the original). Params: `target`*, `axis` (="x"), `center` (="center"), `copy`.
- `copy`: Copy the target, displaced by a vector. Params: `target`*, `by`, `to`, `from`; needs one of by/to.
- `array`: Make several copies in a row (offset) or around an axis (angle). Params: `target`*, `count`*, `offset`, `angle`, `axis` (="z"), `center` (=[0, 0, 0]); needs one of offset/angle.

**Groups, components, materials and variables**

- `group`: Build geometry inside a new group from nested steps. Params: `name`, `steps`*.
- `make_group`: Turn existing geometry into a group. Params: `target`*, `name`.
- `component`: Define a reusable component from nested steps (place it with 'place'). Params: `name`, `steps`*, `description`.
- `make_component`: Turn existing geometry into a component instance. Params: `target`*, `name`, `origin`.
- `place`: Place an instance of a component. Params: `component`*, `position` (=[0, 0, 0]), `rotation`, `scale` (=1), `name`.
- `explode`: Explode groups or component instances back into raw geometry. Params: `target`*.
- `paint`: Apply a material to faces, groups or components. Params: `target`*, `material`*, `face`, `side` (="both").
- `set_tag`: Put entities on a tag (layer); the tag is created if needed. Params: `target`*, `tag`*.
- `hide`: Hide (or show) entities. Params: `target`*, `hidden` (=true).
- `set`: Set (or change) variables for the following steps. Params: `variables`*.

**Erasing and intersecting**

- `erase`: Erase entities (or just some faces of the target). Params: `target`*, `face`.
- `intersect`: Add edges where faces of the target cross other faces (Intersect Faces). Params: `target`*, `with`.

`*` = required. Every step also accepts `id`, `comment`, `material`, `tag`, `if`, `in`, `repeat`.
<!-- END GENERATED OPS -->

### Modelling patterns

* **Wrap each object in its own group.** Raw geometry at the same level sticks together (edges
  split, faces merge), exactly like SketchUp. Primitives (`box`, `cylinder`, `cone`, `sphere`,
  `extrude`) make their own group by default; use `group` with nested `steps` for multi-part objects.
* **Rectangle + push/pull** is the SketchUp way: `rectangle` then `push_pull` with `"face": "top"`.
  Pushing a lone face makes a closed solid; negative distances push behind/into it.
* **Hollow walls**: `rectangle` footprint, `push_pull` up, `offset` the top inward by the wall
  thickness, then `push_pull` the *inner* top face (`{"near": [cx, cy, h]}`) down by the full height.
* **Doors and windows**: `opening` on a solid or group (`"face": "front"`, `origin` = lower-left
  corner of the hole on that face, `width` along the face, `height` up it, `"depth": "through"`).
  Then add a thin glass `box` with an `opacity` material inside the hole.
* **Gable roofs**: `extrude` a triangle drawn in the XZ plane along `"direction": [0, depth, 0]`.
* **Repeated parts** (posts, books, chairs): define a `component` around its own origin, then `place`
  it with `position`, `rotation`, `scale` and `repeat`. Painting the component id paints every instance.
* **Lathes** (vases, domes, columns): draw a half-profile `face` in the XZ plane touching the Z axis,
  a `circle` path around the Z axis, and `follow_me` the profile along the circle; erase the path.
* **Arched openings**: draw a `rectangle` wall face, `line`s and `arc`s on it (they split the face),
  `erase` the arch faces with `{"near": ...}`, then `push_pull` the wall.
* **Tapers and pyramids**: `scale` the `"top"` face of a box (`"factor": [0.3, 0.3, 1]`).
* **Stacking**: `"origin": [x, y, "@table.zmax"]`, or `set` a variable from a query first.

### Common mistakes

* Using Y as up. Z is up; ground is z = 0.
* Forgetting `$` in expressions (`"w / 2"` fails; write `"$w / 2"`).
* Bare numbers are in the file's `units`: with `"units": "m"`, `2400` means 2.4 km.
* A target with several faces needs a `face` selector; `push_pull` on a box id without one fails.
* Raw geometry drawn touching an existing raw face sticks to it (that is how openings work); draw
  separate objects in separate groups, or use `"in": "group_id"` to draw inside a group on purpose.
* `rectangle` sizes are along the drawing plane: in `"plane": "xz"` `width` is X and `depth`/`height`
  is Z. In `"plane": "yz"`, `width` is Y.
* `extrude` with `height` goes along the profile's right-hand-rule normal; use `direction` to be
  explicit (it may be oblique, e.g. a sloped handrail).
* `opening` `origin` must lie on the selected face (the outer front wall face is at the wall's ymin).
* `component` geometry is built around [0, 0, 0]; `place` puts that origin at `position`.
* `repeat.count` must be a whole number: wrap fractional expressions in `floor(...)`.
* Circles and arcs are polygons; increase `segments` (default 24 for circles, 12 for arcs) for
  large curves.
* JSON has no comments; use a `"comment"` key on the step.

## Developing PyModeler

```bash
pip install -e ".[dev]"        # Python 3.11+; on Linux also: apt install libegl1 libgl1 libxkbcommon0
python -m pytest               # all tests (geometry kernel, every op, every example, io, render, CLI)
ruff check pymodeler tests     # lint
python -m pymodeler docs       # regenerate schema, reference docs and the ops list above
```

* Layout: `pymodeler/core` (geometry kernel, no Qt), `ops` (operations + the op registry in
  `ops/registry.py`, bindings in `ops/library.py`), `script` (expressions, engine, validation,
  schema/docs generation), `io` (.pym/OBJ/STL/glTF), `render` (camera, scene, software rasterizer,
  moderngl renderer, previews), `ui` (Qt app, later phases), `cli.py`.
* Adding an op: implement the geometry in `pymodeler/ops/`, register it with `@op(...)` in
  `ops/library.py` (typed `Param`s, summary, example), then run `python -m pymodeler docs` and add a
  test in `tests/script/test_engine.py`. The schema, reference docs, validator and this file's ops list
  all come from the registry, and tests fail if the generated files are stale.
* Internal units are millimetres; tolerance is 0.001 mm (`core/vec.py`). Faces have an outer loop
  (counter-clockwise about the normal) and hole loops. Sticky behaviour lives in `core/sticky.py`
  (`resolve_plane` rebuilds a plane's faces after edges change).
* Never let a bad script crash: raise `GeometryError`/`ScriptError` with a clear message.
* Keep functions small and typed with docstrings; run the tests and ruff before committing.
