# PyModeler

A SketchUp-style 3D modeler written entirely in Python, designed so models can also be
built from **JSON build scripts**: an AI assistant (or you) writes a JSON file, PyModeler
builds it, renders previews, and you iterate.

> **Status: Phase 1 of 7 complete — the geometry kernel.**
> The SketchUp-style geometry engine (sticky edges, faces from loops, holes, healing,
> push/pull, groups/components data model) works headlessly and is covered by tests.
> The JSON build scripts, CLI, exporters and previews arrive in Phase 2, then the GUI.
> See [PLAN.md](PLAN.md) for the roadmap.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/swc0de/3D-CAD-Application.git
cd 3D-CAD-Application
pip install -e ".[dev]"        # or: pip install -r requirements.txt
```

On Linux the Qt GUI and OpenGL previews also need the system libraries
`libegl1 libgl1 libxkbcommon0` (e.g. `sudo apt install libegl1 libgl1 libxkbcommon0`).
The geometry kernel itself only needs numpy.

## Run

```bash
python -m pymodeler            # launches the app (currently prints the build status)
python -m pytest               # run the test suite
ruff check pymodeler tests     # lint
```

## The geometry kernel in 30 seconds

Coordinates are millimetres, Z is up (X red, Y green, Z blue), like SketchUp.

```python
from pymodeler.core.entities import Entities
from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.ops.extrude import push_pull

ents = Entities()

# Four lines that close a loop automatically become a face.
ents.add_polyline([(0, 0, 0), (4000, 0, 0), (4000, 200, 0), (0, 200, 0)], closed=True)
wall = next(iter(ents.faces.values()))

# Push/pull it into a 2.4 m high wall.
push_pull(ents, wall, 2400)

# Draw a door on the front face: the face splits around it (sticky geometry)...
door = ents.add_face([(1000, 0, 0), (1900, 0, 0), (1900, 0, 2100), (1000, 0, 2100)])[0]

# ...and pushing it through the wall cuts an opening.
push_pull(ents, door, -200)

print(is_closed_manifold(ents), signed_volume(ents) / 1e9, "m3")   # True 1.542 m3
```

What the kernel does today:

- **Sticky geometry**: edges split where they cross, touch or overlap; vertices merge
  within 0.001 mm; a closed coplanar loop becomes a face; an edge across a face splits it
  (pieces keep the material); a loop inside a face becomes a hole plus an inner face.
- **Erasing like SketchUp**: erasing the edge between two coplanar faces merges them and
  heals the leftover collinear vertices; erasing a boundary edge removes its faces.
- **Push/pull**: free faces become solids; faces on solids extend, shorten, pocket, raise
  bosses, and cut through (door/window openings); coplanar results merge automatically;
  curved sides (e.g. cylinders) get soft, smooth edges.
- **Model structure**: component definitions and instances with transforms (groups are
  single-use components), materials with opacity, tags with visibility, and world-space
  traversal with material inheritance.
- **Change tracking**: every operation reports created, erased, split and merged entities,
  which the build-script engine will use to keep named references valid.

## Project layout

```
pymodeler/
  core/     geometry kernel (no GUI dependencies)
  ops/      modeling operations (push/pull so far)
  script/   JSON build scripts            (Phase 2)
  io/       .pym, OBJ, STL, glTF/GLB      (Phase 2)
  render/   headless previews, viewport   (Phases 2-3)
  ui/       PySide6 application           (Phases 3-7)
tests/      pytest suite
```

## Roadmap

1. ✅ Geometry kernel
2. JSON build scripts, CLI (`build`, `validate`), exporters, headless PNG previews, examples
3. Basic GUI: viewport, navigation, open/save, run build script
4. Drawing tools with inference snapping and the measurements box
5. Editing tools and undo/redo
6. Groups, components, materials, tags, outliner and entity info
7. Watch-folder live reload, follow-me, intersect faces, tape measure, polish
