"""Wavefront OBJ import (polygons, materials from MTL, one group per object)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pymodeler.core.components import ComponentInstance, add_instance
from pymodeler.core.materials import Material
from pymodeler.core.model import Model
from pymodeler.core.sticky import FaceSpec
from pymodeler.core.units import MM_PER_UNIT, normalize_unit
from pymodeler.core.vec import TOL, GeometryError, Plane, newell_normal, norm
from pymodeler.io.convert import y_up_to_z_up


class ObjImportError(ValueError):
    """Raised when an OBJ file cannot be parsed."""


def import_obj(model: Model, path: str | Path, unit: str = "m", up: str = "y") -> list[ComponentInstance]:
    """Import an OBJ file into ``model`` as one group per OBJ object.

    Coplanar triangles that share edges are merged back into polygons.

    Args:
        unit: Unit of the file's coordinates (default metres).
        up: Up axis of the file, ``"y"`` (default) or ``"z"``.

    Returns:
        The groups created.

    Raises:
        ObjImportError: on malformed content.
        OSError: if the file cannot be read.
    """
    path = Path(path)
    scale = MM_PER_UNIT[normalize_unit(unit)]
    raw_vertices: list[list[float]] = []
    objects: dict[str, list[tuple[list[int], str | None]]] = {}
    current = path.stem
    material: str | None = None
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        key, args = parts[0], parts[1:]
        try:
            if key == "v":
                raw_vertices.append([float(a) for a in args[:3]])
            elif key == "f":
                idx = [_index(a, len(raw_vertices)) for a in args]
                objects.setdefault(current, []).append((idx, material))
            elif key in ("o", "g") and args:
                current = " ".join(args)
            elif key == "usemtl" and args:
                material = args[0]
            elif key == "mtllib" and args:
                _load_mtl(model, path.parent / " ".join(args))
        except (ValueError, IndexError) as exc:
            raise ObjImportError(f"{path.name}:{lineno}: cannot parse {line.strip()!r}") from exc
    verts = np.array(raw_vertices, dtype=float).reshape(-1, 3) * scale
    if up.lower() == "y":
        verts = y_up_to_z_up(verts)
    groups = []
    for name, faces in objects.items():
        definition = model.add_definition(name, is_group=True)
        ents = definition.entities
        specs = []
        for idx, mat in faces:
            specs.extend(_face_specs(verts, idx, mat if mat in model.materials else None))
        if not specs:
            model.remove_definition(definition)
            continue
        ents.add_faces(specs)
        ents.merge_coplanar(list(ents.edges.values()))
        inst = add_instance(model.entities, definition)
        inst.name = name
        groups.append(inst)
    return groups


def _index(token: str, count: int) -> int:
    """0-based vertex index from an OBJ face token like ``7``, ``7/2/3`` or ``-1``."""
    i = int(token.split("/")[0])
    idx = i - 1 if i > 0 else count + i
    if not 0 <= idx < count:
        raise IndexError(f"vertex index {i} out of range")
    return idx


def _face_specs(verts: np.ndarray, idx: list[int], material: str | None) -> list[FaceSpec]:
    """Face requests for one OBJ polygon (fan-triangulated when not planar)."""
    pts = [verts[i] for i in idx]
    if len(pts) < 3 or norm(newell_normal(pts)) < 1e-9:
        return []
    try:
        plane = Plane.from_points(pts)
        planar = all(abs(plane.distance(p)) <= TOL * 10 for p in pts)
    except GeometryError:
        return []
    if planar:
        return [FaceSpec(pts, material=material)]
    out = []
    for k in range(1, len(pts) - 1):
        tri = [pts[0], pts[k], pts[k + 1]]
        if norm(newell_normal(tri)) > 1e-9:
            out.append(FaceSpec(tri, material=material))
    return out


def _load_mtl(model: Model, path: Path) -> None:
    """Add materials from an MTL file (missing files are ignored)."""
    if not path.exists():
        return
    name: str | None = None
    color = (0.8, 0.8, 0.8)
    opacity = 1.0

    def flush() -> None:
        if name and name not in model.materials:
            model.add_material(Material(name, color, opacity))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl" and len(parts) > 1:
            flush()
            name, color, opacity = parts[1], (0.8, 0.8, 0.8), 1.0
        elif parts[0] == "Kd" and len(parts) >= 4:
            color = tuple(min(1.0, max(0.0, float(c))) for c in parts[1:4])  # type: ignore[assignment]
        elif parts[0] == "d" and len(parts) >= 2:
            opacity = float(parts[1])
        elif parts[0] == "Tr" and len(parts) >= 2:
            opacity = 1.0 - float(parts[1])
    flush()
