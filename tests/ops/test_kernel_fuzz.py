"""Randomised invariant checks for sticky geometry and push/pull."""

import random

import numpy as np
import pytest

from pymodeler.core.analysis import is_closed_manifold, signed_volume
from pymodeler.core.entities import Entities
from pymodeler.ops.extrude import push_pull

GRID = range(0, 1001, 50)


def unit_square() -> Entities:
    ents = Entities()
    ents.add_face([(0, 0, 0), (1000, 0, 0), (1000, 1000, 0), (0, 1000, 0)])
    return ents


@pytest.mark.parametrize("seed", range(40))
def test_drawing_on_a_face_always_partitions_it(seed: int) -> None:
    rng = random.Random(seed)
    ents = unit_square()
    for _ in range(rng.randint(1, 6)):
        if rng.random() < 0.6:
            x0, x1 = sorted(rng.sample(GRID, 2))
            y0, y1 = sorted(rng.sample(GRID, 2))
            ents.add_polyline([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], closed=True)
        else:
            a = (rng.choice(GRID), rng.choice([0, 1000]), 0)
            b = (rng.choice(GRID), rng.choice(GRID), 0)
            if a != b:
                ents.add_line(a, b)
    assert sum(f.area() for f in ents.faces.values()) == pytest.approx(1e6)
    for face in ents.faces.values():
        assert np.allclose(face.normal, (0, 0, 1))
        assert all(face in e.faces for e in face.edges())


@pytest.mark.parametrize("seed", range(40))
def test_push_pull_on_box_top_keeps_a_valid_solid(seed: int) -> None:
    rng = random.Random(1000 + seed)
    ents = unit_square()
    push_pull(ents, next(iter(ents.faces.values())), 500)
    x0, x1 = sorted(rng.sample(range(0, 1001, 100), 2))
    y0, y1 = sorted(rng.sample(range(0, 1001, 100), 2))
    if (x0, x1, y0, y1) == (0, 1000, 0, 1000):
        x1 = 900
    distance = rng.choice([-500, -300, -100, 100, 300])
    faces = ents.add_face([(x0, y0, 500), (x1, y0, 500), (x1, y1, 500), (x0, y1, 500)])
    assert len(faces) == 1
    push_pull(ents, faces[0], distance)
    assert is_closed_manifold(ents)
    expected = 1000 * 1000 * 500 + (x1 - x0) * (y1 - y0) * distance
    assert signed_volume(ents) == pytest.approx(expected)
