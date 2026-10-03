"""Tests for the Qt-free document model."""

import json
from pathlib import Path

import pytest

from pymodeler.script.errors import ScriptError
from pymodeler.ui.document import Document, push_recent

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_new_document_is_clean() -> None:
    doc = Document()
    seen = []
    doc.listeners.append(lambda d: seen.append(d.modified))
    doc.new(units="m")
    assert doc.model.units == "m" and not doc.modified and seen == [False]
    assert doc.title == "Untitled - PyModeler"


def test_run_script_save_and_reopen(tmp_path) -> None:
    doc = Document()
    result = doc.run_script(EXAMPLES / "01_box.json")
    assert result.model is doc.model and doc.modified
    assert doc.title.startswith("01_box.json *")
    saved = doc.save(tmp_path / "box")
    assert saved.suffix == ".pym" and not doc.modified
    data = json.loads(saved.read_text())
    assert data["source"]["name"] == "Box"
    other = Document()
    other.open(saved)
    assert other.path == saved and other.source is not None and not other.modified
    assert other.model.stats()["faces"] == 6


def test_failed_build_leaves_document_unchanged(tmp_path) -> None:
    doc = Document()
    doc.run_script(EXAMPLES / "01_box.json")
    before = doc.model
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "steps": [{"op": "box"}]}')
    with pytest.raises(ScriptError):
        doc.run_script(bad)
    assert doc.model is before


def test_save_needs_a_path_and_export(tmp_path) -> None:
    doc = Document()
    with pytest.raises(ValueError):
        doc.save()
    doc.run_script(EXAMPLES / "01_box.json")
    doc.export(tmp_path / "box.glb")
    assert (tmp_path / "box.glb").exists() and doc.path is None


def test_open_obj_marks_modified(tmp_path) -> None:
    doc = Document()
    doc.run_script(EXAMPLES / "01_box.json")
    doc.export(tmp_path / "box.obj")
    doc.open(tmp_path / "box.obj")
    assert doc.modified and doc.path is None


def test_recent_list() -> None:
    recent = push_recent([], "/a.pym")
    recent = push_recent(recent, "/b.pym")
    recent = push_recent(recent, "/a.pym")
    assert [Path(p).name for p in recent] == ["a.pym", "b.pym"]
    for i in range(20):
        recent = push_recent(recent, f"/f{i}.pym")
    assert len(recent) == 8
