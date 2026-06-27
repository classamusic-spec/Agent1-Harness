"""Offline tests for the web console helpers (no sockets)."""

from __future__ import annotations

from harness.server import Console, list_specs


def test_list_specs_reads_repo_specs():
    specs = list_specs("specs")
    names = {s["name"] for s in specs}
    assert "todo-cli" in names
    assert "landing-page" in names
    for s in specs:
        assert {"name", "kind", "path"} <= set(s)


def test_list_specs_missing_dir_is_empty():
    assert list_specs("/nonexistent/specs") == []


def test_console_check_only_job(tmp_path):
    # A check-only job needs no model/key. Point it at an empty workspace so the
    # todo-cli verification fails deterministically.
    console = Console(specs_dir="specs")
    job = console.start({
        "spec": "specs/todo-cli.yaml",
        "workspace": str(tmp_path / "ws"),
        "check_only": True,
    })
    assert job.done.wait(timeout=30)
    assert job.status in ("passed", "failed")  # ran end-to-end
    assert any("RESULT" in line for line in job.lines)
