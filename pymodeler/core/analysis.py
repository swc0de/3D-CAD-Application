"""Geometric diagnostics: solidity, volume, Euler characteristic."""

from __future__ import annotations

import numpy as np

from pymodeler.core.entities import Entities


def is_closed_manifold(entities: Entities) -> bool:
    """True if the faces form closed, consistently oriented surfaces.

    Every edge used by a face must be used by exactly two faces, traversing it in
    opposite directions.  Loose edges without faces are ignored.
    """
    if not entities.faces:
        return False
    for e in entities.edges.values():
        if not e.faces:
            continue
        if len(e.faces) != 2:
            return False
        f1, f2 = list(e.faces)
        d1 = f1.uses_directed(e.v1, e.v2)
        d2 = f2.uses_directed(e.v1, e.v2)
        if d1 is None or d2 is None or d1 == d2:
            return False
    return True


def signed_volume(entities: Entities) -> float:
    """Volume enclosed by the faces (positive when normals point outward), in mm^3.

    Only meaningful for closed manifolds; see :func:`is_closed_manifold`.
    """
    total = 0.0
    for face in entities.faces.values():
        points, tris = face.triangles()
        for a, b, c in tris:
            total += float(np.dot(points[a], np.cross(points[b], points[c])))
    return total / 6.0


def euler_characteristic(entities: Entities) -> int:
    """``V - E + F - H`` where ``H`` counts hole loops (equals ``2 - 2*genus`` per solid)."""
    holes = sum(len(f.loops) - 1 for f in entities.faces.values())
    return len(entities.vertices) - len(entities.edges) + len(entities.faces) - holes


def faces_form_closed_surface(faces: list) -> bool:
    """True if every edge of the given faces is shared by exactly two of them, in
    opposite directions (a closed, consistently oriented shell)."""
    if not faces:
        return False
    members = set(faces)
    for face in faces:
        for e in face.edges():
            users = [f for f in e.faces if f in members]
            if len(users) != 2:
                return False
            d1 = users[0].uses_directed(e.v1, e.v2)
            d2 = users[1].uses_directed(e.v1, e.v2)
            if d1 is None or d2 is None or d1 == d2:
                return False
    return True
