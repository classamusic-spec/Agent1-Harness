"""Offline tests for workspace snapshots, diffs, and failure deltas."""

from __future__ import annotations

from pathlib import Path

from harness.diffing import diff_snapshots, snapshot
from harness.verifier import Check, failure_delta, run_suite


def test_snapshot_reads_text_files(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("x = 1")
    snap = snapshot(str(tmp_path))
    assert snap["a.txt"] == "hello"
    assert snap["sub/b.py"] == "x = 1"


def test_snapshot_skips_noise_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("nope")
    (tmp_path / "keep.txt").write_text("yes")
    snap = snapshot(str(tmp_path))
    assert "keep.txt" in snap
    assert not any("node_modules" in k for k in snap)


def test_diff_added_changed_removed():
    before = {"a.txt": "one\ntwo\n", "gone.txt": "bye"}
    after = {"a.txt": "one\nTWO\n", "new.txt": "hi"}
    d = diff_snapshots(before, after)
    assert "a/a.txt" in d and "+TWO" in d
    assert "new.txt" in d  # added
    assert "gone.txt" in d  # removed


def test_diff_no_change_is_empty():
    snap = {"a.txt": "same"}
    assert diff_snapshots(snap, snap) == ""


def test_failure_delta():
    r1 = run_suite([Check("build", "exit 1"), Check("tests", "exit 1")], stop_on_failure=False)
    r2 = run_suite([Check("build", "true"), Check("tests", "exit 1")], stop_on_failure=False)
    delta = failure_delta(r1, r2)
    assert "now passing: build" in delta
    assert "still failing: tests" in delta


def test_failure_delta_first_round_blank():
    r = run_suite([Check("x", "exit 1")])
    assert failure_delta(None, r) == ""
