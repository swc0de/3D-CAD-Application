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


def test_no_command_launches_the_app(monkeypatch, tmp_path) -> None:
    import pymodeler.ui.app as app

    calls = []
    monkeypatch.setattr(app, "run_app", lambda path=None, watch=None: calls.append((path, watch)) or 0)
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert main(["gui", "box.json"]) == 0
    assert main(["box.json"]) == 0
    (tmp_path / "scripts").mkdir()
    assert main([]) == 0, "a ./scripts folder is watched by default"
    assert main(["gui", "--no-watch"]) == 0
    assert main(["gui", "--watch", "elsewhere"]) == 0
    assert calls == [(None, None), ("box.json", None), ("box.json", None), (None, "scripts"), (None, None),
                     (None, "elsewhere")]


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


def test_watch_once_builds_every_script(tmp_path, capsys) -> None:
    folder = tmp_path / "scripts"
    folder.mkdir()
    (folder / "crate.json").write_text(json.dumps(SCRIPT))
    (folder / "broken.json").write_text('{"version": 1, "steps": [{"op": "box", "size": [1, 1]}]}')
    out = tmp_path / "out"
    code = main(["watch", str(folder), "--once", "--out", str(out), "--size", "80x60", "--renderer", "software"])
    text = capsys.readouterr().out
    assert code == 1, "a failing script makes --once fail"
    assert "built crate.json" in text and "FAIL broken.json" in text and "step 1 (box)" in text
    for name in ("crate.png", "crate_iso.png", "crate_report.json"):
        assert (out / name).exists(), name
    assert not (out / "broken.png").exists()


def test_watch_rebuilds_saved_scripts(tmp_path, capsys, monkeypatch) -> None:
    import argparse
    import time

    from pymodeler.cli import _cmd_watch

    folder = tmp_path / "scripts"
    folder.mkdir()
    script = folder / "crate.json"
    script.write_text(json.dumps(SCRIPT))
    edits = iter([lambda: script.write_text(json.dumps({**SCRIPT, "steps": [
        {"op": "box", "id": "crate", "size": [2, 0.5, 0.5]}]}))])

    def fake_sleep(_seconds: float) -> None:
        edit = next(edits, None)
        if edit is not None:
            edit()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    args = argparse.Namespace(folder=str(folder), out=str(tmp_path / "out"), once=False, interval=0.0,
                              no_preview=True, views="iso", size="80x60", renderer="software")
    assert _cmd_watch(args, cycles=3) == 0
    text = capsys.readouterr().out
    assert text.count("built crate.json") == 2, text
    assert "size 2m x 0.5m x 0.5m" in text.splitlines()[-1]
    report = json.loads((tmp_path / "out" / "crate_report.json").read_text())
    assert report["objects"]["crate"]["bounds"]["size"] == [2.0, 0.5, 0.5]


def test_watch_missing_folder(tmp_path, capsys) -> None:
    assert main(["watch", str(tmp_path / "nope"), "--once"]) == 1
    assert "no such folder" in capsys.readouterr().err
