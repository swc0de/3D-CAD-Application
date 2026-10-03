# Build script reference

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
* a number with a unit: `"2.4m"`, `"300mm"`, `"8ft"`, `"6in"`, `"8' 6""`, `"45deg"`, `"0.5rad"`
* an expression: `"$w / 2 - $wall"`, `"max($a, 300mm)"`, `"@table.zmax + 10"`

Expressions support `+ - * / // % ^`, comparisons (`< <= > >= == !=`), `and`, `or`, `not`,
the constants `pi`, `e`, `true`, `false`, and these functions (trigonometry in degrees):
`sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sqrt`, `abs`, `min`, `max`, `round`, `floor`, `ceil`, `pow`, `hypot`, `clamp`, `if`, `int`.

**Geometry queries** `@id.property` read the world bounding box of an earlier step's result,
in file units: `xmin`, `xmax`, `ymin`, `ymax`, `zmin`, `zmax`, `xmid`, `ymid`, `zmid`, `width`, `depth`, `height`. Use `@id[2].zmax` for one part of a repeated step.

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
that lie furthest along it (so `"top"` is a box's lid): `top`, `up`, `+z`, `z`, `bottom`, `down`, `-z`, `right`, `+x`, `x`, `left`, `-x`, `back`, `+y`, `y`, `front`, `-y`. Also `"all"`, `"largest"`,
`"smallest"`, `{"normal": [x, y, z]}`, `{"near": [x, y, z]}` and `{"index": n}`. If the target is
a single face (just drawn), `face` can be omitted. Selecting the back of a lone face (e.g.
`"bottom"` of a rectangle drawn on the ground) is allowed and works on its other side.

## Operations

- **Drawing**: [`line`](#line), [`rectangle`](#rectangle), [`circle`](#circle), [`arc`](#arc), [`polygon`](#polygon), [`face`](#face), [`guide`](#guide)
- **Primitive solids**: [`box`](#box), [`cylinder`](#cylinder), [`cone`](#cone), [`sphere`](#sphere)
- **Modifying faces**: [`push_pull`](#push_pull), [`follow_me`](#follow_me), [`offset`](#offset), [`extrude`](#extrude), [`opening`](#opening)
- **Moving and copying**: [`move`](#move), [`rotate`](#rotate), [`scale`](#scale), [`mirror`](#mirror), [`copy`](#copy), [`array`](#array)
- **Groups, components, materials and variables**: [`group`](#group), [`make_group`](#make_group), [`component`](#component), [`make_component`](#make_component), [`place`](#place), [`explode`](#explode), [`paint`](#paint), [`set_tag`](#set_tag), [`hide`](#hide), [`set`](#set)
- **Erasing and intersecting**: [`erase`](#erase), [`intersect`](#intersect)

## Drawing

### `line`

Draw connected straight edges. A closed, flat loop becomes a face.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `points` | points |  | two or more points to connect in order |
| `from` | point |  | start point (use with 'to') |
| `to` | point |  | end point (use with 'from') |
| `closed` | bool | `false` | also connect the last point back to the first |

Requires one of: `points`, `from`.

```json
{"op": "line", "id": "outline", "points": [[0, 0, 0], [2000, 0, 0], [2000, 1000, 0]], "closed": true}
```

### `rectangle`

Draw a rectangular face from a corner (or its centre).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `origin` | point | `[0, 0, 0]` | corner of the rectangle (its centre if center is true) |
| `width` | length | **required** | size along the plane's first axis (X for xy and xz, Y for yz) |
| `depth` / `height` | length | **required** | size along the plane's second axis (Y for xy, Z for xz and yz) |
| `plane` | `xy` \| `xz` \| `yz` | `"xy"` | drawing plane: xy (ground, faces +Z), xz (faces front, -Y), yz (faces right, +X) |
| `normal` | direction |  | front direction of the shape; overrides 'plane' |
| `x_axis` | direction |  | direction of the shape's local X axis within its plane |
| `center` | bool | `false` | treat origin as the centre |

```json
{"op": "rectangle", "id": "floor", "origin": [0, 0, 0], "width": "$w", "depth": "$d"}
```

### `circle`

Draw a circular face (a polygon with many sides, marked as one curve).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre point |
| `radius` | length | **required** | radius |
| `segments` | integer | `24` | number of straight segments approximating the curve |
| `plane` | `xy` \| `xz` \| `yz` | `"xy"` | drawing plane: xy (ground, faces +Z), xz (faces front, -Y), yz (faces right, +X) |
| `normal` | direction |  | front direction of the shape; overrides 'plane' |

```json
{"op": "circle", "id": "disc", "center": [0, 0, 0], "radius": 500, "segments": 32}
```

### `arc`

Draw an arc of a circle; optionally close it into a face.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre of the circle |
| `radius` | length | **required** | radius |
| `start_angle` | angle | `0` | start angle, counter-clockwise from the plane's first axis |
| `end_angle` | angle | **required** | end angle |
| `segments` | integer | `12` | number of segments |
| `close` | `none` \| `chord` \| `pie` | `"none"` | none (open curve), chord (straight closing edge) or pie (through the centre) |
| `plane` | `xy` \| `xz` \| `yz` | `"xy"` | drawing plane: xy (ground, faces +Z), xz (faces front, -Y), yz (faces right, +X) |
| `normal` | direction |  | front direction of the shape; overrides 'plane' |
| `x_axis` | direction |  | direction of the shape's local X axis within its plane |

```json
{"op": "arc", "id": "arch", "center": [0, 0, 0], "radius": 1000, "start_angle": 0, "end_angle": 180, "plane": "xz", "close": "chord"}
```

### `polygon`

Draw a regular polygon face.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre point |
| `radius` | length | **required** | distance from centre to the corners (or edge midpoints) |
| `sides` | integer | **required** | number of sides (3 or more) |
| `inscribed` | bool | `true` | corners on the radius (true) or edge midpoints on it (false) |
| `plane` | `xy` \| `xz` \| `yz` | `"xy"` | drawing plane: xy (ground, faces +Z), xz (faces front, -Y), yz (faces right, +X) |
| `normal` | direction |  | front direction of the shape; overrides 'plane' |

```json
{"op": "polygon", "id": "hex", "center": [0, 0, 0], "radius": 300, "sides": 6}
```

### `face`

Draw a face from an outline of points (any flat polygon, optionally with holes).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `points` | points | **required** | outline points in order |
| `holes` | loops |  | outlines of holes inside the face |
| `normal` | direction |  | front direction of the shape; overrides 'plane' |

```json
{"op": "face", "id": "gable", "points": [[0, 0, 2400], [4000, 0, 2400], [2000, 0, 3400]]}
```

### `guide`

Add a construction guide: a dashed line through a point, or a guide point.

Guides show in the app (where the cursor snaps to them) and are saved in .pym files, but they are not geometry: they are not exported and not shown in previews.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `point` | point | **required** | a point the guide passes through (or the guide point) |
| `direction` | direction |  | direction of a guide line; leave out for a guide point |

```json
{"op": "guide", "point": [0, 0, 900], "direction": "x"}
```

## Primitive solids

### `box`

An axis-aligned box (in its own group by default).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `origin` | point | `[0, 0, 0]` | minimum corner (or base centre when center is true) |
| `size` | size |  | [width, depth, height] |
| `width` | length |  | size along X |
| `depth` | length |  | size along Y |
| `height` | length |  | size along Z |
| `center` | bool | `false` | centre the box on origin in X and Y |
| `group` | bool | `true` | wrap the solid in its own group so it never sticks to other geometry |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

Requires one of: `size`, `width`.

```json
{"op": "box", "id": "top", "origin": [0, 0, 720], "size": [1600, 900, 30], "material": "oak"}
```

### `cylinder`

A cylinder standing on its base centre (in its own group by default).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre of the base |
| `radius` | length | **required** | radius |
| `height` | length | **required** | height along the axis |
| `segments` | integer | `24` | number of straight segments approximating the curve |
| `axis` | direction | `"z"` | direction from base to top |
| `group` | bool | `true` | wrap the solid in its own group so it never sticks to other geometry |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

```json
{"op": "cylinder", "id": "tower", "center": [0, 0, 0], "radius": 2000, "height": 8000, "segments": 48}
```

### `cone`

A cone, frustum (top_radius > 0) or pyramid (segments 4) on its base centre.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre of the base |
| `radius` | length | **required** | base radius |
| `height` | length | **required** | height along the axis |
| `top_radius` | length | `0` | radius of a flat top (0 makes a point) |
| `segments` | integer | `24` | number of straight segments approximating the curve |
| `axis` | direction | `"z"` | direction from base to tip |
| `group` | bool | `true` | wrap the solid in its own group so it never sticks to other geometry |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

```json
{"op": "cone", "id": "roof", "center": [0, 0, 8000], "radius": 2400, "height": 3000}
```

### `sphere`

A sphere (in its own group by default).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `center` | point | `[0, 0, 0]` | centre |
| `radius` | length | **required** | radius |
| `segments` | integer | `24` | number of straight segments approximating the curve |
| `rings` | integer | `12` | segments from pole to pole |
| `group` | bool | `true` | wrap the solid in its own group so it never sticks to other geometry |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

```json
{"op": "sphere", "id": "ball", "center": [0, 0, 500], "radius": 500}
```

## Modifying faces

### `push_pull`

Extrude a face along its normal (SketchUp's Push/Pull).

A lone face becomes a closed solid. Pushing a face of a solid adds material or cuts a pocket; pushing all the way through the opposite face cuts a hole. New faces join the target's reference.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | which face(s) of the target; may be omitted when the target is a single face |
| `distance` | length | **required** | how far to push (negative pushes into the solid) |
| `create_new` | bool | `false` | keep the original face in place (like holding Ctrl) |

New faces are added to the target's reference.

```json
{"op": "push_pull", "target": "floor", "face": "top", "distance": "$h"}
```

### `follow_me`

Sweep a profile face along a path (SketchUp's Follow Me).

Place the profile at the start of the path (for a path of edges: at either end, or at any corner of a closed loop), perpendicular to it. A closed circular path around an axis makes a lathe (vases, domes, rings).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | which face(s) of the target; may be omitted when the target is a single face |
| `path` | path | **required** | points of the path, or the id of earlier lines/arcs |
| `closed` | bool |  | treat the path as a closed loop (default: closed if it ends where it starts) |

New faces are added to the target's reference.

```json
{"op": "follow_me", "target": "profile", "path": [[0, 0, 0], [2000, 0, 0], [2000, 2000, 0]]}
```

### `offset`

Draw a copy of a face's outline inside (positive) or outside (negative) it.

The new outline splits the face, so the inner face can then be pushed or pulled (e.g. hollow out a box, or make a picture frame).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | which face(s) of the target; may be omitted when the target is a single face |
| `distance` | length | **required** | offset distance; positive is inward |

New faces are added to the target's reference.

```json
{"op": "offset", "target": "box", "face": "top", "distance": 50}
```

### `extrude`

Make a prism: a face from an outline, pushed along a direction.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `points` | points | **required** | outline of the profile |
| `height` | length |  | distance along the profile's normal (right-hand rule on the points) |
| `direction` | vector |  | explicit extrusion vector (instead of height) |
| `group` | bool | `true` | wrap the solid in its own group so it never sticks to other geometry |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

Requires one of: `height`, `direction`.

```json
{"op": "extrude", "id": "roof", "points": [[0, 0, 2400], [4000, 0, 2400], [2000, 0, 3400]], "direction": [0, 3000, 0]}
```

### `opening`

Cut a rectangular opening (door, window) into a face of a solid or group.

Width runs horizontally along the face (left to right as seen from outside) and height runs up it; on a horizontal face width runs along X and height along Y.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face | **required** | the face to cut into, e.g. "front" |
| `origin` | point | **required** | lower-left corner of the opening, on (or near) the face |
| `width` | length | **required** | horizontal size along the face |
| `height` | length | **required** | vertical size along the face |
| `depth` | depth | `"through"` | how deep to cut, or "through" to cut through the wall |

```json
{"op": "opening", "target": "walls", "face": "front", "origin": [1200, 0, 0], "width": 900, "height": 2100}
```

## Moving and copying

### `move`

Move the target by a vector, or from one point to another.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | move only these faces (connected geometry stretches) |
| `by` | vector |  | displacement [dx, dy, dz] |
| `to` | point |  | destination of the 'from' point |
| `from` | point |  | reference point (default: the target's bounding-box minimum) |

Requires one of: `by`, `to`.

```json
{"op": "move", "target": "table", "by": [0, 500, 0]}
```

### `rotate`

Rotate the target about an axis.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | rotate only these faces |
| `angle` | angle | **required** | degrees, counter-clockwise looking down the axis |
| `axis` | direction | `"z"` | rotation axis |
| `center` | anchor | `"center"` | pivot point |

```json
{"op": "rotate", "target": "chair", "angle": 90, "axis": "z", "center": "center"}
```

### `scale`

Scale the target uniformly or per axis.

Scaling a single face (e.g. the top of a box) about its centre makes tapers and pyramids.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | scale only these faces (e.g. the top of a box to taper it) |
| `factor` | scale | **required** | scale factor, or [sx, sy, sz] |
| `center` | anchor | `"center"` | fixed point |

```json
{"op": "scale", "target": "box", "face": "top", "factor": 0.5}
```

### `mirror`

Mirror the target across a plane (optionally keeping the original).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `axis` | direction | `"x"` | normal of the mirror plane ("x" flips left/right) |
| `center` | anchor | `"center"` | a point on the mirror plane |
| `copy` | bool | `false` | keep the original and add a mirrored copy |

```json
{"op": "mirror", "target": "left_wing", "axis": "x", "center": [0, 0, 0], "copy": true}
```

### `copy`

Copy the target, displaced by a vector.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `by` | vector |  | displacement of the copy |
| `to` | point |  | where the 'from' point of the copy goes |
| `from` | point |  | reference point (default: the target's bounding-box minimum) |

Requires one of: `by`, `to`.

```json
{"op": "copy", "id": "chair2", "target": "chair", "by": [800, 0, 0]}
```

### `array`

Make several copies in a row (offset) or around an axis (angle).

Each copy becomes one part of the step's id, so 'posts[2]' is the third copy.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `count` | integer | **required** | number of copies to add (the original stays) |
| `offset` | vector |  | displacement between neighbours (linear array) |
| `angle` | angle |  | angle between neighbours (polar array) |
| `axis` | direction | `"z"` | polar array axis |
| `center` | anchor | `[0, 0, 0]` | polar array centre |

Requires one of: `offset`, `angle`.

```json
{"op": "array", "id": "posts", "target": "post", "count": 4, "offset": [1000, 0, 0]}
```

## Groups, components, materials and variables

### `group`

Build geometry inside a new group from nested steps.

Geometry in a group never sticks to anything outside it. Nested steps use the same coordinates as the parent, and their ids stay usable afterwards.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string |  | name shown in the outliner (also usable as a reference) |
| `steps` | steps | **required** | steps to run inside the group |

```json
{"op": "group", "id": "table", "name": "Table", "steps": [{"op": "box", "id": "top", "origin": [0, 0, 720], "size": [1600, 900, 30]}]}
```

### `make_group`

Turn existing geometry into a group.

References to the grouped geometry now point at the group.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

```json
{"op": "make_group", "target": "floor", "name": "Room"}
```

### `component`

Define a reusable component from nested steps (place it with 'place').

The component is not placed by this step. Its geometry is built around [0, 0, 0], which becomes the insertion point used by 'place'.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string |  | name shown in the outliner (also usable as a reference) |
| `steps` | steps | **required** | steps that build the component around its own origin |
| `description` | string |  | description of the component |

```json
{"op": "component", "id": "post", "steps": [{"op": "cylinder", "center": [0, 0, 0], "radius": 50, "height": 1000, "group": false}]}
```

### `make_component`

Turn existing geometry into a component instance.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `name` | string |  | name shown in the outliner (also usable as a reference) |
| `origin` | point |  | insertion point (default: bounding-box minimum) |

```json
{"op": "make_component", "target": "leg", "name": "Leg"}
```

### `place`

Place an instance of a component.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `component` | component | **required** | id of the component to place |
| `position` | point | `[0, 0, 0]` | where the component's origin goes |
| `rotation` | rotation | `0` | rotation in degrees about Z, or {"axis": ..., "angle": ...} |
| `scale` | scale | `1` | scale factor or [sx, sy, sz] |
| `name` | string |  | name shown in the outliner (also usable as a reference) |

```json
{"op": "place", "component": "post", "position": [0, 0, 0], "repeat": {"count": 5, "offset": [1000, 0, 0]}}
```

### `explode`

Explode groups or component instances back into raw geometry.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |

```json
{"op": "explode", "target": "table"}
```

### `paint`

Apply a material to faces, groups or components.

Painting a group or component colours all of its unpainted faces.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `material` | material | **required** | material name (null resets to the default) |
| `face` | face |  | only paint these faces of the target |
| `side` | `front` \| `back` \| `both` | `"both"` | which side of faces to paint |

```json
{"op": "paint", "target": "Room", "material": "brick"}
```

### `set_tag`

Put entities on a tag (layer); the tag is created if needed.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `tag` | string | **required** | tag name |

```json
{"op": "set_tag", "target": "roof", "tag": "Roof"}
```

### `hide`

Hide (or show) entities.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `hidden` | bool | `true` | true hides, false shows |

```json
{"op": "hide", "target": "helper_lines"}
```

### `set`

Set (or change) variables for the following steps.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `variables` | variables | **required** | names and values; expressions may use geometry queries |

```json
{"op": "set", "variables": {"top": "@table.zmax"}}
```

## Erasing and intersecting

### `erase`

Erase entities (or just some faces of the target). Alias: `delete`.

With 'face', only those faces go and their edges stay (as in SketchUp). Without it the whole target goes, including edges left bounding nothing.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `face` | face |  | only erase these faces of the target |

```json
{"op": "erase", "target": "construction_lines"}
```

### `intersect`

Add edges where faces of the target cross other faces (Intersect Faces).

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | target | **required** | what to operate on |
| `with` | target |  | what to intersect with (default: everything else in the model) |

```json
{"op": "intersect", "target": "roof", "with": "walls"}
```

## Parameter types

| Type | Accepts |
| --- | --- |
| length | a length: number in file units, unit string like "2.4m", or expression |
| number | a plain number or expression |
| integer | a whole number or expression |
| angle | an angle in degrees (or "0.5rad") or expression |
| bool | true or false (or an expression) |
| string | text |
| enum | one of a fixed set of words |
| point | a point [x, y, z] (z may be omitted for 0) |
| vector | a displacement [dx, dy, dz] |
| direction | an axis name like "z" or "-x", or a vector [x, y, z] |
| points | a list of points |
| loops | a list of point lists (hole outlines) |
| size | [x, y, z] lengths |
| scale | a factor, or per-axis factors [sx, sy, sz] |
| anchor | a point, or one of "center", "min", "max", "bottom", "top" of the target's box |
| rotation | degrees about Z, or {"axis": ..., "angle": ...} |
| target | the id of an earlier step ("name" or "name[2]"), a list of ids, or "*" for everything |
| face | a face selector: "top", "bottom", "front", "back", "left", "right", "+x".."-z", "all", "largest", "smallest", {"normal": [x,y,z]}, {"near": [x,y,z]} or {"index": n} |
| material | a material name (declared in "materials", or a colour name / "#rrggbb") |
| component | the id of a component defined earlier with the "component" op |
| path | a list of points, or the id of earlier lines/arcs to follow |
| steps | a list of nested steps |
| variables | an object of variable names to values/expressions |
| depth | a length, or "through" to cut through the solid |
