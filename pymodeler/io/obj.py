"""Wavefront OBJ export (with a companion MTL file)."""

from __future__ import annotations

from pathlib import Path

from pymodeler.core.model import Model
from pymodeler.io.convert import mm_scale, z_up_to_y_up
from pymodeler.io.mesh import build_mesh


def export_obj(model: Model, path: str | Path, unit: str = "m", up: str = "y") -> None:
    """Write ``path`` (.obj) and a sibling .mtl.

    Args:
        unit: Length unit of the written coordinates (default metres).
        up: ``"y"`` (most tools) or ``"z"`` (keep PyModeler's axes).
    """
    path = Path(path)
    mtl_path = path.with_suffix(".mtl")
    mesh = build_mesh(model)
    scale = mm_scale(unit)
    lines = [f"# PyModeler export ({unit}, {up.upper()} up)", f"mtllib {mtl_path.name}"]
    mtl = ["# PyModeler materials"]
    v_base = 1
    for name, part in mesh.parts.items():
        positions, normals = part.arrays()
        if not len(positions):
            continue
        if up == "y":
            positions, normals = z_up_to_y_up(positions), z_up_to_y_up(normals)
        positions = positions * scale
        safe = _safe_name(name)
        lines.append(f"o {safe}")
        lines.append(f"usemtl {safe}")
        lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in positions)
        lines.extend(f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in normals)
        for t in range(len(positions) // 3):
            a, b, c = (v_base + 3 * t + k for k in range(3))
            lines.append(f"f {a}//{a} {b}//{b} {c}//{c}")
        v_base += len(positions)
        r, g, b = part.color
        mtl += [f"newmtl {safe}", f"Kd {r:.4f} {g:.4f} {b:.4f}", "Ka 0 0 0", "Ks 0 0 0", f"d {part.opacity:.4f}", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtl_path.write_text("\n".join(mtl) + "\n", encoding="utf-8")


def _safe_name(name: str) -> str:
    """Material/object names without whitespace."""
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "material"

