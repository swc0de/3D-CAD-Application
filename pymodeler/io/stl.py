"""Binary STL export."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from pymodeler.core.model import Model
from pymodeler.io.convert import mm_scale
from pymodeler.io.mesh import build_mesh


def export_stl(model: Model, path: str | Path, unit: str = "mm") -> int:
    """Write a binary STL (Z up, default millimetres, as 3D printers expect).

    Returns:
        Number of triangles written.
    """
    mesh = build_mesh(model)
    chunks = [p.arrays()[0] for p in mesh.parts.values() if p.positions]
    tris = (np.vstack(chunks) if chunks else np.zeros((0, 3))).reshape(-1, 3, 3) * mm_scale(unit)
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(lengths, 1e-12)
    header = b"PyModeler STL export".ljust(80, b" ")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack("<I", len(tris)))
        record = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        record["n"] = normals
        record["v"] = tris
        fh.write(record.tobytes())
    return len(tris)
