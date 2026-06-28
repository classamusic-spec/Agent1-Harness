"""Tests for per-turn workspace snapshots, diffs, and rollback."""

from __future__ import annotations

from pathlib import Path

from harness import versions


def _write(ws: Path, name: str, text: str) -> None:
    (ws / name).write_text(text)


def test_snapshot_assigns_increasing_ids(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "index.html", "<h1>one</h1>")
    m1 = versions.snapshot(ws, label="Initial build")
    m2 = versions.snapshot(ws, label="Change")
    assert m1["id"] == 1 and m2["id"] == 2
    listed = versions.list_versions(str(ws))
    assert [v["id"] for v in listed] == [1, 2]
    assert listed[0]["label"] == "Initial build"


def test_snapshot_skips_markers_and_version_store(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "index.html", "<h1>hi</h1>")
    _write(ws, ".studio.json", "{}")
    _write(ws, ".appbuilder_checkpoint.json", "{}")
    meta = versions.snapshot(ws)
    assert meta["files"] == ["index.html"]
    # snapshotting again must not capture the .studio versions dir
    meta2 = versions.snapshot(ws)
    assert meta2["files"] == ["index.html"]


def test_diff_against_current_reports_modified_added(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "index.html", "line a\nline b\n")
    versions.snapshot(ws, label="v1")
    _write(ws, "index.html", "line a\nline B changed\n")
    _write(ws, "app.js", "console.log(1)\n")
    files = versions.diff(str(ws), 1, None)  # v1 -> current
    by = {f["path"]: f for f in files}
    assert by["index.html"]["status"] == "modified"
    assert by["app.js"]["status"] == "added"
    assert by["index.html"]["added"] >= 1 and by["index.html"]["removed"] >= 1
    assert "line B changed" in by["index.html"]["diff"]


def test_diff_between_two_versions(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "a.txt", "1\n")
    versions.snapshot(ws)
    _write(ws, "a.txt", "2\n")
    versions.snapshot(ws)
    files = versions.diff(str(ws), 1, 2)
    assert files and files[0]["status"] == "modified"


def test_diff_no_changes_is_empty(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "a.txt", "same\n")
    versions.snapshot(ws)
    assert versions.diff(str(ws), 1, None) == []


def test_restore_rolls_back_and_records_new_version(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "index.html", "original")
    versions.snapshot(ws, label="v1")
    _write(ws, "index.html", "edited")
    _write(ws, "extra.js", "added later")
    versions.snapshot(ws, label="v2")
    restored = versions.restore(str(ws), 1)
    assert (ws / "index.html").read_text() == "original"
    assert not (ws / "extra.js").exists()  # files not in v1 are removed
    assert restored["id"] == 3
    assert "Reverted to v1" in restored["label"]


def test_restore_unknown_version_raises(tmp_path):
    ws = tmp_path / "app"; ws.mkdir()
    _write(ws, "a.txt", "x")
    versions.snapshot(ws)
    try:
        versions.restore(str(ws), 99)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass
