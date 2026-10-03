"""glTF 2.0 export, as binary ``.glb`` or embedded ``.gltf`` (metres, Y up)."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from pymodeler.core.model import Model
from pymodeler.io.convert import srgb_to_linear, z_up_to_y_up
from pymodeler.io.mesh import build_mesh

_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_FLOAT = 5126
_UNSIGNED_INT = 5125


def gltf_document(model: Model) -> tuple[dict[str, Any], bytes]:
    """Build the glTF JSON document and its binary buffer."""
    mesh = build_mesh(model)
    blob = bytearray()
    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "PyModeler"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": model.name or "Model"}],
        "meshes": [{"primitives": []}],
        "materials": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
    }
    for name, part in mesh.parts.items():
        positions, normals = part.arrays()
        if not len(positions):
            continue
        positions = (z_up_to_y_up(positions) / 1000.0).astype(np.float32)
        normals = z_up_to_y_up(normals).astype(np.float32)
        indices = np.arange(len(positions), dtype=np.uint32)
        pos_acc = _add_accessor(doc, blob, positions, "VEC3", _FLOAT, _ARRAY_BUFFER, with_bounds=True)
        nrm_acc = _add_accessor(doc, blob, normals, "VEC3", _FLOAT, _ARRAY_BUFFER)
        idx_acc = _add_accessor(doc, blob, indices, "SCALAR", _UNSIGNED_INT, _ELEMENT_ARRAY_BUFFER)
        r, g, b = (srgb_to_linear(c) for c in part.color)
        material: dict[str, Any] = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [r, g, b, part.opacity],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": True,
        }
        if part.opacity < 0.999:
            material["alphaMode"] = "BLEND"
        doc["materials"].append(material)
        doc["meshes"][0]["primitives"].append(
            {
                "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                "indices": idx_acc,
                "material": len(doc["materials"]) - 1,
            }
        )
    if not doc["meshes"][0]["primitives"]:
        doc["nodes"] = [{"name": model.name or "Model"}]
        del doc["meshes"], doc["materials"], doc["accessors"], doc["bufferViews"]
    doc["buffers"] = [{"byteLength": len(blob)}] if blob else []
    if not blob:
        doc.pop("buffers")
    return doc, bytes(blob)


def _add_accessor(
    doc: dict[str, Any],
    blob: bytearray,
    data: np.ndarray,
    kind: str,
    component: int,
    target: int,
    with_bounds: bool = False,
) -> int:
    """Append ``data`` to the buffer with a bufferView and accessor; returns its index."""
    while len(blob) % 4:
        blob.append(0)
    raw = data.tobytes()
    doc["bufferViews"].append(
        {"buffer": 0, "byteOffset": len(blob), "byteLength": len(raw), "target": target}
    )
    blob.extend(raw)
    accessor: dict[str, Any] = {
        "bufferView": len(doc["bufferViews"]) - 1,
        "componentType": component,
        "count": int(len(data)),
        "type": kind,
    }
    if with_bounds:
        accessor["min"] = [float(x) for x in data.min(axis=0)]
        accessor["max"] = [float(x) for x in data.max(axis=0)]
    doc["accessors"].append(accessor)
    return len(doc["accessors"]) - 1


def export_glb(model: Model, path: str | Path) -> None:
    """Write a binary glTF (``.glb``)."""
    doc, blob = gltf_document(model)
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if blob:
        chunks += struct.pack("<II", len(blob), 0x004E4942) + blob
    header = struct.pack("<III", 0x46546C67, 2, 12 + len(chunks))
    Path(path).write_bytes(header + chunks)


def export_gltf(model: Model, path: str | Path) -> None:
    """Write a text glTF (``.gltf``) with the buffer embedded as base64."""
    doc, blob = gltf_document(model)
    if blob:
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()
    Path(path).write_text(json.dumps(doc, indent=1), encoding="utf-8")
