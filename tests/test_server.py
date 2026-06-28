"""Offline tests for the web console helpers (no sockets)."""

from __future__ import annotations

import time

import pytest

from harness.server import (
    Console,
    list_artifacts,
    list_specs,
    read_workspace_file,
    save_spec,
    workspace_tree,
)
from harness.spec import SpecError, load_spec


def test_list_specs_reads_repo_specs():
    specs = list_specs("specs")
    names = {s["name"] for s in specs}
    assert "todo-cli" in names
    assert "landing-page" in names
    for s in specs:
        assert {"name", "kind", "path"} <= set(s)


def test_list_specs_missing_dir_is_empty():
    assert list_specs("/nonexistent/specs") == []


def test_save_spec_valid_then_loadable(tmp_path):
    path = save_spec(str(tmp_path), {
        "name": "My App", "kind": "frontend", "language": "html",
        "description": "build a thing",
        "constraints": ["no frameworks"],
        "verification": [{"name": "files", "command": "test -f index.html"}],
    })
    assert path.endswith("my-app.yaml")
    spec = load_spec(path)
    assert spec.name == "My App" and spec.kind == "frontend"
    assert spec.checks[0].command == "test -f index.html"


def test_save_spec_invalid_rejected(tmp_path):
    with pytest.raises(SpecError):
        save_spec(str(tmp_path), {"name": "x"})  # no description


def test_list_artifacts(tmp_path):
    (tmp_path / "app1").mkdir()
    (tmp_path / "app1" / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / "app1" / "styles.css").write_text("body{}")
    (tmp_path / "app2").mkdir()
    (tmp_path / "app2" / "main.py").write_text("print(1)")
    arts = {a["name"]: a for a in list_artifacts(str(tmp_path))}
    assert arts["app1"]["has_index"] and arts["app1"]["files"] == 2
    assert not arts["app2"]["has_index"]


def test_workspace_tree_and_read(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    tree = workspace_tree(str(tmp_path))
    assert any(f["path"] == "a.txt" for f in tree["files"])
    assert read_workspace_file(str(tmp_path), "a.txt") == "hello"


def test_read_workspace_file_confined(tmp_path):
    (tmp_path / "secret").mkdir()
    with pytest.raises(FileNotFoundError):
        read_workspace_file(str(tmp_path / "secret"), "../a.txt")


def test_server_approval_roundtrip():
    import asyncio
    import threading
    from harness.approval import Decision
    from harness.server import Job, ServerApproval

    job = Job("j1", "/tmp/ws")
    gate = ServerApproval(job, timeout=5)
    result = {}

    def run():
        result["decision"] = asyncio.run(gate.request("build", {"workspace": "/tmp/ws"}))

    t = threading.Thread(target=run)
    t.start()
    # Wait for the gate to register the pending request, then approve it.
    for _ in range(50):
        if job.pending:
            break
        time.sleep(0.02)
    assert job.pending and job.pending["kind"] == "build"
    job.decision = Decision(True, "ship it")
    job.approve_event.set()
    t.join(timeout=5)
    assert result["decision"].approved is True
    assert job.pending is None


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
