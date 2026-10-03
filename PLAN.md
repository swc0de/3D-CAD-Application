# PyModeler — Plan

A SketchUp-style desktop 3D modeler in pure Python (PySide6 + moderngl + numpy), whose
models can also be built headlessly from versioned JSON **build scripts**, so an AI
assistant can write a JSON file, build it, look at PNG previews, and iterate.

## 1. Architecture

```
            ┌─────────────── ui/ (PySide6) ───────────────┐
            │ MainWindow · Tools · Inference · VCB · Panels│
            └──────┬───────────────────────────┬──────────┘
                   │ commands                  │ draws
   script/ ────────▼──────┐             ┌──────▼──────┐
   JSON → validate → ops  │             │   render/   │  GL viewport + headless
            ops/ (registry of commands, undo/redo)    │  offscreen/software PNG
                   │                    └──────▲──────┘
            core/ (geometry kernel, no Qt) ────┘
                   │
            io/ (.pym, OBJ, STL, glTF/GLB, OBJ import)
```

Hard rule: `core/`, `ops/`, `script/`, `io/` and the headless part of `render/` never import Qt,
so everything except the GUI runs (and is tested) headless.

**Key design decisions**

- **Internal units: millimetres** (float64), Z up, right-handed: X = red, Y = green, Z = blue.
  Geometric tolerance 1e-3 mm. Conversions happen only at the edges (JSON, VCB, export:
  glTF is written in metres).
- **Kernel = SketchUp-style edge/face graph per context.** An `Entities` collection (model root,
  group or component definition) owns vertices, edges and faces. Faces are planar, have one
  outer loop plus any number of hole loops, and front/back materials. Geometry in different
  contexts never sticks together (that is what groups are for).
- **Sticky geometry via a planar re-solve.** Adding an edge merges coincident vertices, splits
  crossing/overlapping/touching edges, then for each affected plane rebuilds that plane's 2D
  arrangement (half-edge traversal, dangling edges pruned, nested loops become holes).
  An arrangement region becomes a face if it lies inside an existing face (it inherits that face's
  material and orientation, which is how faces split) or if a newly added edge closes it (new face).
  Removing an edge between two coplanar faces merges them the same way.
- **Push/pull** builds the cap and side faces, then applies SketchUp's rules: a free face keeps its
  original (flipped) as the back; a face on a solid moves; coplanar results merge; a cap landing on an
  opposite-facing face cancels it out, which is what punches door/window openings through a wall.
- **Ops are registered commands.** Each op is a typed function `op(model, ctx, **params) -> OpResult`
  in a single registry (`@register_op`) that also declares its parameters. The GUI tools, the JSON
  engine, the JSON Schema and the reference docs all come from that registry, so they can't drift.
  `OpResult` reports created/deleted/split entities so named references stay valid.
- **Undo/redo** via a command stack holding snapshots of the contexts each command touched
  (simple and robust; deltas can come later).
- **Rendering:** moderngl shaders shared by the Qt viewport (`QOpenGLWidget`) and the offscreen
  renderer (EGL/standalone context). If no GL context can be created (e.g. a headless box without
  libEGL), previews fall back to a **pure-numpy software rasterizer** (z-buffer, flat shading, edge
  lines), so `--preview` always works.

## 2. Folder layout

```
pymodeler/
  __main__.py, cli.py        # `python -m pymodeler [gui|build|validate|render|info|docs]`
  core/      vec.py (math, planes, tolerance), units.py, transform.py, entities.py
             (Vertex/Edge/Face/Entities), sticky.py (splitting, plane re-solve, erase/heal),
             planar.py (2D arrangement, point-in-polygon, triangulation with holes),
             changes.py (ids + change log), components.py (definitions/instances),
             model.py (Model, placements), analysis.py (manifold/volume checks),
             materials.py, tags.py
  ops/       base.py (registry, OpResult, Command, UndoStack), draw.py (line, rectangle,
             circle, arc, polygon, face), extrude.py (push_pull, follow_me, offset),
             transform.py (move, rotate, scale, copy, array), edit.py (erase, delete, intersect),
             organize.py (group, component, place, explode, paint, tag), primitives.py
             (box, cylinder, cone, sphere: macros built from the ops above)
  script/    values.py (units, $variables, safe math expressions), selectors.py (face selectors),
             refs.py (named references), engine.py (step executor, repeat, nested contexts),
             schema.py (generates the JSON Schema from the op registry), errors.py
  io/        native.py (.pym = JSON), mesh.py (triangulated export mesh), obj.py, stl.py,
             gltf.py (glTF + GLB), obj_import.py
  render/    camera.py, scene.py (model → triangle/line buffers), gl_renderer.py (moderngl),
             software.py (numpy rasterizer), offscreen.py (PNG previews + 2×2 contact sheet)
  ui/        main_window.py, viewport.py, inference.py, vcb.py, watcher.py,
             tools/ (select, line, rectangle, circle, arc, polygon, push_pull, move, rotate,
             scale, offset, tape, paint, eraser, orbit, pan, zoom, follow_me),
             panels/ (outliner, entity_info, materials, tags)
schema/build_script.schema.json   # generated, test checks it's in sync with the registry
docs/BUILD_SCRIPT_REFERENCE.md    # generated from the schema
examples/                         # ≥ 8 build scripts (box … tower with cone roof)
scripts/                          # default watch folder for live reload
tests/                            # pytest: kernel, every op, every example, io, render, CLI
CLAUDE.md, README.md, pyproject.toml, requirements.txt
```

## 3. Build-script format (draft, extends your example)

- Top level: `version` (1), `units` (default `mm`), `variables`, `materials`, `tags`, `steps`.
- Values: numbers in file units, unit strings (`"2.4m"`, `"300mm"`, `"8ft"`, `"6in"`),
  expressions (`"$w / 2 + 50mm"`, with `min/max/sin/cos/sqrt/round/pi`, angles in degrees),
  and geometry queries of earlier objects (`"@table.zmax"`, `"@room.xcen"`).
- Every step: `op`, optional `id`, `comment`, `material`, `tag`, `if` (an expression), and `repeat`
  (`count` plus `offset`, or a polar `rotate`, with loop variable `$i`, so stairs can use
  `"z": "$i * 180"`).
- Named references: `id` names the set of entities a step produced. Later ops that modify it keep
  it up to date (after `push_pull`, `"floor"` means the whole box). Indexing a repeated result
  works too: `"post[2]"`.
- Face selectors: `"top"`, `"bottom"`, `"+x"`/`"-y"`/…, `"front"`/`"back"`/`"left"`/`"right"`,
  `{"normal": [x,y,z]}`, `{"near": [x,y,z]}`, `"largest"`, and `{"index": n}`.
- Contexts: `group` and `component` take nested `steps` (isolated geometry); `place` instances a
  component; `"in": "Room"` runs a step inside an existing group.
- Validation: the JSON Schema checks the structure, then each step is checked against its own op's
  schema. Errors read like `step 4 (push_pull): missing required parameter 'distance'`.
  Runtime errors (unknown reference, a face that can't be selected) report the step number too.
  A bad file never crashes the app or CLI.
- CLI: `build file.json -o out.pym --export out.glb --preview out.png [--report]` writes iso,
  front, top and right PNGs, a 2×2 contact sheet with labelled bounding-box dimensions (one image
  for Claude to look at), and an optional JSON report of every named object's bounding box.

## 4. Milestones

Each phase ends with: the app runs, `pytest` passes, README updated, commit, push.

Progress: **Phase 1 done** (kernel + push/pull, 200 tests including randomised invariant
checks). Next: Phase 2.

1. **Kernel.** pyproject/requirements, package skeleton, `core/` (vectors, planes, units, transforms,
   entities, planar arrangement, triangulation, model/groups/components/materials/tags), plus
   `push_pull` on a free rectangle. Tests: faces from loops, edge splitting (crossing, T-junction,
   collinear overlap), face split by an edge, hole from an inner loop, push/pull box,
   coplanar merge. GitHub Actions CI running pytest.
2. **Scripting before GUI.** Op registry with all ops, the values/expression parser, selectors,
   references, repeat and contexts, the engine, schema generation, the `build`/`validate`/`render`/
   `info`/`docs` CLI, `.pym` save/load, OBJ/STL/GLB export, OBJ import, software and GL offscreen
   previews, the 8+ examples (all building in tests), CLAUDE.md, the generated reference docs, and
   README with preview screenshots.
3. **Basic GUI.** Main window, GL viewport with orbit/pan/zoom-to-cursor, zoom extents, standard
   views, perspective/parallel, axes, grid, sky/ground, edge+face display, open/save/recent files,
   File → Run Build Script…
4. **Drawing tools.** Line, rectangle, circle, arc, polygon; inference engine (endpoint, midpoint,
   on-edge, on-face, axis lock with red/green/blue colouring, parallel/perpendicular, hint
   tooltips); VCB for typed lengths/radii/segment counts; status-bar hints.
5. **Editing tools.** Select (click, shift, window/crossing, double/triple-click), push/pull, move
   (with copy and `5x` arrays via VCB), rotate (protractor), scale, offset, eraser, undo/redo.
6. **Organisation.** Groups/components (make, edit in context, explode), paint bucket and Materials
   panel, Tags panel with visibility, Outliner, Entity Info.
7. **Live and polish.** Watch-folder live rebuild, follow-me, intersect faces, tape measure (guides),
   then performance, docs and final README.

## 5. Known limitations (planned simplifications, updated as we go)

- The kernel is tolerance-based floating point, not exact arithmetic. Degenerate inputs (near-zero
  edges, almost-coplanar faces) are snapped or rejected with an error.
- Curves are segmented polylines (as in SketchUp). Edges between faces of curved surfaces are
  marked *soft* so they render smooth. There are no true NURBS.
- Undo stores context snapshots, which costs memory on very large models.
- Offset and follow-me use mitred joints and don't resolve self-intersections.
- Intersect Faces handles planar faces only.
- Materials are colour plus opacity; there are no textures in the first version.
- Only a subset of SketchUp's inference is implemented.
- On Linux, the GUI and GL previews need the system `libegl1`/`libgl1` packages. The software
  renderer needs neither.
- Each plane re-solve scans every edge in its context, so cost grows with the size of a
  single flat context (a 96-segment cylinder push/pull takes about 0.2 s). Groups and
  components keep contexts small; a spatial index can be added if needed.
