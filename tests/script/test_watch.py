"""The Qt-free folder watcher behind live rebuilds."""

import os

from pymodeler.script.watch import FolderWatcher


def touch(path, text: str, mtime_ns: int) -> None:
    path.write_text(text)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_reports_new_and_modified_scripts_once_settled(tmp_path) -> None:
    touch(tmp_path / "a.json", "{}", 1_000_000_000)
    watcher = FolderWatcher(tmp_path)
    assert watcher.poll() == [], "existing scripts are not reported by default"
    touch(tmp_path / "a.json", '{"v": 1}', 2_000_000_000)
    touch(tmp_path / "b.json", "{}", 3_000_000_000)
    assert watcher.poll() == [], "a change is reported once the file stops changing"
    assert watcher.poll() == [tmp_path / "a.json", tmp_path / "b.json"]
    assert watcher.poll() == []
    touch(tmp_path / "b.json", '{"v": 2}', 4_000_000_000)
    watcher.poll()
    touch(tmp_path / "b.json", '{"v": 22}', 5_000_000_000)
    assert watcher.poll() == [], "still being written"
    assert watcher.poll() == [tmp_path / "b.json"]


def test_unsettled_mode_reports_immediately_and_ignores_backups(tmp_path) -> None:
    watcher = FolderWatcher(tmp_path, settle=False)
    touch(tmp_path / "model.json", "{}", 1_000_000_000)
    for junk in (".model.json.swp", ".#model.json", "model.json~", "~model.json", "notes.txt"):
        (tmp_path / junk).write_text("x")
    assert watcher.poll() == [tmp_path / "model.json"]
    (tmp_path / "model.json").unlink()
    assert watcher.poll() == []
    touch(tmp_path / "model.json", "{}", 1_000_000_000)
    assert watcher.poll() == [tmp_path / "model.json"], "a re-created file counts as changed"


def test_report_existing_and_latest(tmp_path) -> None:
    touch(tmp_path / "old.json", "{}", 1_000_000_000)
    touch(tmp_path / "new.json", "{}", 9_000_000_000)
    watcher = FolderWatcher(tmp_path, report_existing=True, settle=False)
    assert watcher.poll() == [tmp_path / "old.json", tmp_path / "new.json"]
    assert watcher.latest() == tmp_path / "new.json"
    assert FolderWatcher(tmp_path / "missing").poll() == []
    assert FolderWatcher(tmp_path / "missing").latest() is None
