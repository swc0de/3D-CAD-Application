"""Tests for the command-line interface."""

import json

import pytest

from pymodeler.cli import main

SCRIPT = {
    "version": 1,
    "units": "m",
    "materials": {"wood": "#b07d4f"},
    "steps": [{"op": "box", "id": "crate", "size": [1, 0.5, 0.5], "material": "wood"}],
}


@pytest.fixture
def script_file(tmp_path):
    path = tmp_path / "crate.json"
    path.write_text(json.dumps(SCRIPT))
    return path


def test_no_command_prints_status(capsys) -> None:
    assert main([]) == 0
    assert "PyModeler" in capsys.readouterr().out


def test_build_with_outputs(tmp_path, script_file, capsys) -> None:
    out = tmp_path / "out"
    code = main([
        "build", str(script_file), "-o", str(out / "crate.pym"), "--export", str(out / "crate.glb"),
        "--export", str(out / "crate.stl"), "--preview", str(out / "crate.png"), "--size", "120x90",
        "--renderer", "software", "--report", str(out / "report.json"),
    ])
    assert code == 0, capsys.readouterr().err
    for name in ("crate.pym", "crate.glb", "crate.stl", "crate.png", "crate_iso.png", "crate_top.png", "report.json"):
        assert (out / name).exists(), name
    report = json.loads((out / "report.json").read_text())
    assert report["objects"]["crate"]["bounds"]["size"] == [1.0, 0.5, 0.5]
    assert report["objects"]["crate"]["solid"] is True
    saved = json.loads((out / "crate.pym").read_text())
    assert saved["source"]["steps"][0]["op"] == "box", "the script is embedded in the .pym"


def test_build_report_to_stdout(script_file, capsys) -> None:
    assert main(["build", str(script_file), "--report", "-q"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["units"] == "m" and report["bounds"]["size"] == [1.0, 0.5, 0.5]


def test_build_errors_are_reported_not_raised(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "steps": [{"op": "box"}]}')
    assert main(["build", str(bad)]) == 1
    assert "step 1 (box)" in capsys.readouterr().err
    broken = tmp_path / "broken.json"
    broken.write_text('{"version": 1,, }')
    assert main(["build", str(broken)]) == 1
    assert "invalid JSON at line 1" in capsys.readouterr().err
    assert main(["build", str(tmp_path / "missing.json")]) == 1
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"version": 1, "steps": [{"op": "circle", "radius": -1}]}')
    assert main(["build", str(runtime)]) == 1
    assert "radius must be positive" in capsys.readouterr().err


def test_validate_command(tmp_path, script_file, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "steps": [{"op": "cirle"}]}')
    assert main(["validate", str(script_file)]) == 0
    assert main(["validate", str(script_file), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "OK" in out and "did you mean 'circle'" in out


def test_render_export_info(tmp_path, script_file, capsys) -> None:
    pym = tmp_path / "crate.pym"
    assert main(["export", str(script_file), str(pym), str(tmp_path / "crate.obj")]) == 0
    assert (tmp_path / "crate.mtl").exists()
    assert main(["render", str(pym), "--preview", str(tmp_path / "r.png"), "--size", "64x48",
                 "--renderer", "software", "--views", "iso,back"]) == 0
    assert (tmp_path / "r_back.png").exists()
    assert main(["info", str(script_file)]) == 0
    assert "crate" in capsys.readouterr().out
    assert main(["info", str(pym), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["units"] == "m"
    assert main(["render", str(pym), "--preview", str(tmp_path / "x.png"), "--views", "sideways"]) == 1
    assert main(["export", str(pym), str(tmp_path / "x.3ds")]) == 1
    assert main(["render", str(pym), "--preview", str(tmp_path / "x.png"), "--size", "big"]) == 1


def test_ops_and_docs_check(capsys) -> None:
    assert main(["ops"]) == 0
    assert "push_pull" in capsys.readouterr().out
    assert main(["docs", "--check"]) == 0
