# PyModeler

A SketchUp-style 3D modeler written entirely in Python, built so models can also be created
from **JSON build scripts**. An AI assistant like Claude (or you) writes a JSON file,
PyModeler builds it, renders preview images, and you iterate.

![The ten example models, rendered by the headless preview renderer](docs/images/examples.png)

> **Status: all seven phases are complete.**
> - A SketchUp-style geometry kernel (sticky geometry, faces with holes, push/pull).
> - The build-script pipeline: JSON scripts, validation, the CLI, export to OBJ/STL/glTF/GLB,
>   `.pym` save/load, headless PNG previews, and a watch folder for live rebuilds.
> - The desktop app: a 3D viewport with SketchUp navigation, inference snapping and the
>   Measurements box; drawing, editing and construction tools; groups and components; materials
>   and tags; the Outliner and Entity Info panels; undo/redo.
>
> See [PLAN.md](PLAN.md) for the design and its known limitations.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/swc0de/3D-CAD-Application.git
cd 3D-CAD-Application
pip install -e ".[dev]"        # or: pip install -r requirements.txt
```

On Linux the GUI and the OpenGL preview renderer need the system libraries
`libegl1 libgl1 libxkbcommon0` (`sudo apt install libegl1 libgl1 libxkbcommon0`). Without
them, previews automatically fall back to a pure-numpy software renderer, so
`--preview` works on any machine, including headless servers.

## Quick start

```bash
python -m pymodeler build examples/04_house.json \
    -o out/house.pym --export out/house.glb --preview out/house.png --report
```

This builds the model, saves it, exports a GLB, and writes `out/house.png` (below) plus one PNG per
view. `--report` prints every named object's bounding box as JSON.

![Preview contact sheet of the example house](docs/images/house_preview.png)

## The app

```bash
python -m pymodeler                     # start the app
python -m pymodeler examples/04_house.json   # (or: python -m pymodeler gui <file>) open a model or script
```

![PyModeler showing the example house](docs/images/app_window.png)

| Action | How |
| --- | --- |
| Orbit | Middle-drag, or the Orbit tool (**O**) with left-drag |
| Pan | Shift + middle-drag, or the Pan tool (**H**) |
| Zoom | Scroll wheel (zooms toward the cursor), or the Zoom tool (**Z**) with drag |
| Zoom extents | **Shift+Z** |
| Re-centre on a point | Double-click the middle button |
| Standard views | Iso **F8**, Top **F2**, Front **F3**, Right **F4**, Back **F5**, Left **F6**, Bottom **F7** |
| Perspective / parallel | **F10** |
| Run a build script | File > Run Build Script (**Ctrl+R**). **F9** rebuilds it after you (or Claude) edit the file. |
| Live rebuild | File > Live Rebuild watches a folder of scripts (see [Live rebuild](#live-rebuild)). |
| Open / Save / Export | **Ctrl+O** / **Ctrl+S** / **Ctrl+E**. File > Open Recent lists recent files. |
| Units and model name | File > Model Info |
| Every shortcut | Help > Keyboard Shortcuts (**F1**) |

The tools are in a palette on the left, grouped as in SketchUp: selection, drawing, editing,
construction and camera.

The app opens `.pym` models, imports `.obj` files and builds `.json` scripts. Build errors are
shown in a dialog naming the failing step, and the current model is kept.

### Drawing

![Drawing a rectangle on a face and a line locked to the red axis](docs/images/drawing_tools.png)

| Tool | Key | Use |
| --- | --- | --- |
| Line | **L** | Click points; lines chain until you press Esc, double-click, or close a face. |
| Rectangle | **R** | Click two opposite corners. Draws on the face under the cursor, or on the ground. |
| Circle | **C** | Click the centre, then the radius. |
| Arc | **A** | Click the start, the end, then pull out the bulge (2-point arc). |
| Polygon | — | Click the centre, then the radius. The default is 6 sides. |

- **Inference snapping** works like SketchUp's. The cursor snaps to endpoints (green), midpoints
  (cyan), the origin, points on edges (red) and points on faces (blue). From the previous point
  it also locks to the red, green or blue axis, or runs parallel or perpendicular (magenta) to the
  last edge you hovered. A tooltip names the snap.
- **Locks**: the arrow keys lock a direction (→ red, ← green, ↑ blue; press again to unlock), and
  holding **Shift** keeps the current inference. For Rectangle and Circle, the arrows choose the
  drawing plane instead.
- **Measurements box**: just start typing and press Enter.
  - Line: a length (`2500`, `2.5m`, `8' 6"`), absolute coordinates `[x, y, z]`, or a relative
    offset `<dx, dy, dz>`.
  - Rectangle: `width,depth`.
  - Circle and Polygon: a radius, or `32s` for 32 segments or sides.
  - Arc: the bulge, or a segment count.
- **Undo / Redo**: **Ctrl+Z** and **Ctrl+Y** (or **Ctrl+Shift+Z**). Every tool action is one undo step.

### Editing

![Push/Pull previewing an extrusion; the selected cylinder top is highlighted](docs/images/editing_tools.png)

| Tool | Key | Use |
| --- | --- | --- |
| Select | **Space** | Click selects; Shift toggles, Ctrl adds, Ctrl+Shift removes. Drag right for a window selection, left for a crossing selection. Double-click selects a face with its edges; triple-click selects everything connected. Clicking a group selects the whole group. |
| Eraser | **E** | Click or drag over edges, groups and guides. Shift hides instead, Ctrl softens. |
| Push/Pull | **P** | Click a face, move, click (or drag). Type a distance. Ctrl keeps the original face; double-click repeats the last distance. |
| Move | **M** | Click a point, then the destination; Ctrl copies. After a copy, type `5x` for five in a row or `/5` to divide the gap. |
| Rotate | **Q** | Click the centre, a reference point, then the angle. The protractor snaps every 15°, and you can type an angle. Arrow keys pick the axis. |
| Scale | **S** | Click a fixed point and a handle, then move. Type `2` or `1.5,1,1`. |
| Offset | **F** | Click a face, then move in or out. Type a distance (positive is inward). |

- **Edit menu**: Delete (**Del**), Select All (**Ctrl+A**), Select None (**Ctrl+T**), Hide, Unhide All.
- With nothing selected, Move, Rotate and Scale act on whatever you click.
- **Right-click** opens a context menu for what is under the cursor. While a tool is in the
  middle of an operation, right-click cancels it instead.

### Groups, components, materials and tags

![Editing inside the roof group: the rest of the model is faded, the group's box is shown, and the side panels are docked on the right](docs/images/groups_panels.png)

| Command | How | What it does |
| --- | --- | --- |
| Make Group | **Ctrl+G** | Groups the selection. Groups keep their geometry from sticking to anything else. |
| Make Component | **G** | Like a group, but named and reusable: copies share one definition, so editing one edits them all. |
| Edit a group | Double-click it, or Edit > Edit Group/Component | Opens the group. Everything else fades, and the tools draw and edit inside the group in its own coordinates. |
| Close a group | **Esc** (Select tool), click outside it, or Edit > Close Group/Component | Returns to the parent context. |
| Explode | Edit > Explode | Replaces a group or component by its contents, which then stick to the surrounding geometry. |
| Make Unique | Edit > Make Unique | Gives the selected component copies their own definition. Copied *groups* become unique automatically when you open one. |
| Paint Bucket | **B** | Click a face to paint the side you are looking at, or a group to paint the whole group. If the clicked face is selected, the whole selection is painted. **Alt**+click picks up a material. |

The panels are docked on the right; reopen a closed one from View > Panels.

- **Entity Info**: what is selected, with the face area or edge length. You can change the
  name (groups and components), material, tag, hidden flag and soft edges.
- **Materials**: click a material to start painting with it. New, Edit (colour and opacity)
  and Delete. Deleting a material resets whatever used it to the default.
- **Tags**: tick boxes show and hide tags; Add and Delete.
- **Outliner**: the tree of groups and components. Click selects (opening parent groups as
  needed); double-click opens one for editing. The group being edited is shown in bold.

Every change made in a panel is a single undoable step.

### Construction tools

![A molding swept around a box top with Follow Me; dashed guide lines, a guide point, and the Tape Measure measuring from an edge](docs/images/tape_follow_me.png)

| Tool | Key | Use |
| --- | --- | --- |
| Tape Measure | **T** | Click two points to measure. Starting on an edge measures square to it and leaves a parallel guide line; starting on a point leaves a guide point. Type a length for an exact offset. After measuring, type a new length to resize the model (or the open group) to match. **Ctrl** turns guide creation off and on. |
| Follow Me | — | Sweep a profile face along a path. Either select the path (edges, or a face whose edge is the path) and click the profile, or click the profile and then an edge of the path. An edge of a face means "around this face". |
| Intersect Faces | Edit > Intersect Faces | Adds edges where the selected faces and groups cross the whole model, each other, or the rest of the open group. |

- **Guides** are dashed construction lines that the cursor snaps to ("On Guide", "Guide Point").
  They are saved in `.pym` files but are not geometry, so they are never exported.
  Show or hide them with View > Guides; remove them with the Eraser or Edit > Delete Guides.
  Build scripts can add them with the `guide` operation.

## Build scripts

A build script is an ordered list of modeling steps. Each step calls one operation, the same
operations the app's tools use.

```json
{
  "version": 1,
  "units": "mm",
  "variables": { "w": 4000, "d": 3000, "h": 2400 },
  "materials": { "brick": "#a0522d", "glass": { "color": "#88ccff", "opacity": 0.4 } },
  "steps": [
    { "op": "rectangle", "id": "room", "width": "$w", "depth": "$d" },
    { "op": "push_pull", "target": "room", "face": "top", "distance": "$h" },
    { "op": "make_group", "target": "room", "name": "Room" },
    { "op": "opening", "target": "Room", "face": "front", "origin": [1500, 0, 0],
      "width": 900, "height": 2100, "depth": "through" },
    { "op": "paint", "target": "Room", "material": "brick" },
    { "op": "component", "id": "post", "steps": [
        { "op": "cylinder", "radius": 50, "height": 1000, "group": false } ] },
    { "op": "place", "component": "post", "position": [0, -1000, 0],
      "repeat": { "count": 5, "offset": [1000, 0, 0] } }
  ]
}
```

Highlights:

- **34 operations**:
  - Drawing: `line`, `rectangle`, `circle`, `arc`, `polygon`, `face`, `guide`.
  - Modifying faces: `push_pull`, `follow_me`, `offset`, `extrude`, `opening`.
  - Moving and copying: `move`, `rotate`, `scale`, `mirror`, `copy`, `array`.
  - Organising: `group`, `make_group`, `component`, `make_component`, `place`, `explode`, `paint`,
    `set_tag`, `hide`.
  - Editing: `erase`, `intersect`.
  - Variables: `set`.
  - Primitives: `box`, `cylinder`, `cone`, `sphere`.
- **Units** at file level (`mm`, `cm`, `m`, `in`, `ft`) and per value (`"2.4m"`, `"300mm"`,
  `"8' 6\""`).
- **Variables and expressions**: `"$w / 2 - 300mm"`, with functions, comparisons and conditions.
- **Geometry queries**: `"@table.zmax"` reads an earlier object's bounding box, which makes stacking easy.
- **Named references** that survive edits: a rectangle's id names the whole solid after `push_pull`,
  and names the group after `make_group`.
- **Face selectors**: `"top"`, `"front"`, `"-x"`, `{"normal": [...]}`, `{"near": [...]}`, and more.
- **`repeat`** with `count`, `offset`, polar `rotate` or a list of `values`. Each iteration gets
  `$i`, and `id[n]` addresses one copy.
- **Validation** against a JSON Schema
  ([schema/build_script.schema.json](schema/build_script.schema.json)) plus static checks. Errors
  name the step, for example `step 4 (push_pull): missing required parameter 'distance'`.
  A bad file never crashes anything.

Read [CLAUDE.md](CLAUDE.md) for the workflow, patterns and common mistakes, and
[docs/BUILD_SCRIPT_REFERENCE.md](docs/BUILD_SCRIPT_REFERENCE.md) for every operation and
parameter. The [examples/](examples) folder has ten scripts: a box, a table, a chair, a house with
door and window openings, stairs, a fence, a bookshelf built from components, a round tower with a
cone roof, a lathed vase and an arched aqueduct.

## Command line

| Command | What it does |
| --- | --- |
| `python -m pymodeler [file]` | Launch the app (optionally opening a `.pym`, `.obj` or `.json`). With no file, it watches `./scripts` if that folder exists. |
| `python -m pymodeler gui [file] [--watch DIR \| --no-watch]` | Launch the app, choosing the folder to watch for live rebuilds. |
| `python -m pymodeler watch [DIR] [--out out] [--once]` | Rebuild every script in `DIR` (default `scripts`) whenever one is saved, writing `out/<name>.png` previews and `out/<name>_report.json`. |
| `python -m pymodeler build script.json [-o model.pym] [--export f.glb] [--preview p.png] [--report]` | Build a script; save, export, preview, report. |
| `python -m pymodeler validate script.json ...` | Check scripts without building them. |
| `python -m pymodeler render model.pym --preview p.png [--views iso,front] [--size 1200x900]` | Preview a `.pym`, `.obj` or `.json`. |
| `python -m pymodeler export model.pym out.stl out.obj` | Convert between formats. |
| `python -m pymodeler info model.pym` | Print sizes, materials and named objects. |
| `python -m pymodeler ops` | List every operation and its parameters. |
| `python -m pymodeler docs [--check]` | Regenerate the schema, the reference docs and CLAUDE.md's ops list. |

Exports:

| Format | Units | Up axis | Notes |
| --- | --- | --- | --- |
| glTF / GLB | metres | Y up | Materials keep their colour and opacity. |
| OBJ + MTL | metres | Y up | |
| STL | millimetres | Z up | Binary STL. |
| `.pym` | — | — | The native format. Stores the full topology plus the build script, so models stay editable. |

`import` of OBJ files goes into one group per object, and coplanar triangles are merged back into faces.

## Using it with Claude

Ask Claude to write a build script, then let it run the loop:

1. Write `scripts/thing.json`.
2. Run `python -m pymodeler validate scripts/thing.json`.
3. Run `python -m pymodeler build scripts/thing.json --preview out/thing.png --report`.
4. Look at the PNG and the report, fix the script, and repeat.

### Live rebuild

The `scripts/` folder is a watch folder. Start the app from the repository root
(`python -m pymodeler`) and it shows the newest script there, then rebuilds whenever any script
in the folder is saved, by you or by Claude. Rebuilding the script you are looking at keeps the
camera where it is. If the new version fails to build, the error appears in a banner over the
view and the last good model stays on screen:

![A live rebuild error banner naming the failing step, over the last good model](docs/images/live_rebuild.png)

Hand edits made in the app since the last build are protected: the app offers to save them before
a rebuild replaces the model. File > Live Rebuild turns watching on and off, and File > Watch
Folder picks another folder.

Without the app, `python -m pymodeler watch` does the same headlessly. It rebuilds each saved
script and writes `out/<name>.png` and `out/<name>_report.json`, so Claude can look at the
result after every edit.

## The geometry kernel

The kernel lives in `pymodeler/core` and has no GUI dependencies. It behaves like SketchUp's
"sticky" geometry:

- Edges split where they cross, touch or overlap.
- A closed loop of coplanar edges becomes a face.
- An edge drawn across a face splits it, and the pieces keep the material.
- A loop drawn inside a face becomes a hole plus an inner face.
- Erasing the edge between two coplanar faces heals them back into one.
- Push/pull follows SketchUp's rules:
  - A lone face becomes a solid.
  - A face of a solid extends it or cuts a pocket.
  - Pushing all the way through cuts a hole.
  - Coplanar results merge.

```python
from pymodeler.core.entities import Entities
from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.ops.extrude import push_pull

ents = Entities()
ents.add_polyline([(0, 0, 0), (4000, 0, 0), (4000, 200, 0), (0, 200, 0)], closed=True)
wall = next(iter(ents.faces.values()))
push_pull(ents, wall, 2400)                                   # a 2.4 m wall
door = ents.add_face([(1000, 0, 0), (1900, 0, 0), (1900, 0, 2100), (1000, 0, 2100)])[0]
push_pull(ents, door, -200)                                   # cut the door through it
print(is_closed_manifold(ents), signed_volume(ents) / 1e9)   # True 1.542
```

## Project layout

```
pymodeler/
  core/     geometry kernel: vertices/edges/faces, sticky geometry, groups & components
  ops/      every modeling operation + the op registry shared by scripts and the GUI
  script/   expressions, build-script engine, validation, schema & docs generation
  io/       .pym, OBJ/STL/glTF/GLB export, OBJ import
  render/   camera, scene, numpy software rasterizer, moderngl renderer, previews
  ui/       PySide6 app: main window, OpenGL viewport, navigation, tools, side panels
schema/     generated JSON Schema for build scripts
docs/       generated reference + images
examples/   example build scripts
tests/      pytest suite
```

## Development

```bash
python -m pytest               # run the tests (about 500; under a minute)
xvfb-run -a python -m pytest   # Linux without a display: also exercises the OpenGL viewport
ruff check pymodeler tests     # lint
python -m pymodeler docs       # regenerate the schema/docs after changing an operation
```

## Roadmap

1. ✅ Geometry kernel
2. ✅ JSON build scripts, CLI, exporters, headless previews, examples
3. ✅ Basic GUI: viewport, navigation, open/save, run build script
4. ✅ Drawing tools with inference snapping and the measurements box (plus undo/redo)
5. ✅ Editing tools and undo/redo
6. ✅ Groups, components, materials, tags, outliner and entity info
7. ✅ Watch-folder live rebuild, Follow Me, Intersect Faces and Tape Measure tools, polish
