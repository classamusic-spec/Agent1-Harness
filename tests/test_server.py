"""Offline tests for the web console helpers (no sockets)."""

from __future__ import annotations

import time

import pytest

from harness.server import (
    Console,
    list_artifacts,
    list_specs,
    read_spec,
    save_spec,
    read_workspace_file,
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


def test_save_then_read_spec_round_trips_scaffold_and_run(tmp_path):
    path = save_spec(str(tmp_path), {
        "name": "Notes", "kind": "fullstack", "language": "python",
        "description": "a notes app", "scaffold": "python-db", "run": "python server.py",
        "verification": [{"name": "health", "command": "curl $APP_URL/api/health",
                          "needs_server": True}],
    })
    s = read_spec(path)
    assert s["scaffold"] == "python-db" and s["run"] == "python server.py"
    assert s["kind"] == "fullstack"
    assert s["verification"][0]["name"] == "health"


def test_persona_prompt_varies_by_kind():
    from harness import personas
    fe = personas.system_prompt("frontend")
    be = personas.system_prompt("backend")
    assert fe != be
    assert len(fe) > 100 and len(be) > 100  # real composed prompts, not labels


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


def test_ship_and_deploy_sets_deploy_url(tmp_path, monkeypatch):
    from harness import deploy as dep
    from harness import server as srv
    from harness import ship as shp
    ws = tmp_path / "app"; ws.mkdir(); (ws / "index.html").write_text("<x>")
    monkeypatch.setattr(shp, "write_export", lambda w, **k: ["Dockerfile"])
    monkeypatch.setattr(dep, "run", lambda prov, w, n, **k: {"ok": True, "url": "https://app.fly.dev"})
    c = srv.Console(str(tmp_path), str(tmp_path))
    job = srv.Job("j1", str(ws))
    c._ship_and_deploy(job, "fly")
    assert job.deploy_url == "https://app.fly.dev"


def test_ship_and_deploy_handles_missing_cli(tmp_path, monkeypatch):
    from harness import deploy as dep
    from harness import server as srv
    from harness import ship as shp
    ws = tmp_path / "app"; ws.mkdir()
    monkeypatch.setattr(shp, "write_export", lambda w, **k: [])
    monkeypatch.setattr(dep, "run", lambda prov, w, n, **k: {
        "ok": False, "ready": False, "reason": "fly CLI not found", "commands": ["fly deploy"],
        "url": "https://app.fly.dev"})
    c = srv.Console(str(tmp_path), str(tmp_path))
    job = srv.Job("j2", str(ws))
    c._ship_and_deploy(job, "fly")
    assert job.deploy_url == "https://app.fly.dev"  # would-be URL still surfaced


def test_build_queue_runs_specs_in_order(tmp_path, monkeypatch):
    import os
    from harness import server as srv

    order = []

    def fake_run(self, job, params):
        order.append(os.path.basename(job.workspace))
        job.status = "passed"
        job.done.set()

    monkeypatch.setattr(srv.Console, "_run", fake_run)
    c = srv.Console(specs_dir="specs", workspaces_dir=str(tmp_path))

    j1 = c.enqueue({"spec": "specs/todo-cli.yaml", "workspace": str(tmp_path / "a")})
    j2 = c.enqueue({"spec": "specs/todo-cli.yaml", "workspace": str(tmp_path / "b")})
    assert j1.status == "queued" or j1.done.is_set()
    # let the worker drain
    for _ in range(200):
        if len(order) == 2:
            break
        time.sleep(0.02)
    assert order == ["a", "b"]   # ran back-to-back, in order


def test_queue_status_shape(tmp_path, monkeypatch):
    from harness import server as srv
    c = srv.Console(specs_dir="specs", workspaces_dir=str(tmp_path))
    st = c.queue_status()
    assert set(st) >= {"running", "current", "pending"}
    assert st["pending"] == []


def test_leaderboard_runs_models_and_ranks(tmp_path, monkeypatch):
    from harness import leaderboard, server as srv

    def fake_run_model(base_url, model, *, root=None, build_fn=None):
        speed = {"glm-4.6": 80, "qwen": 40}.get(model, 10)
        return {"model": model, "ok": True, "rounds": 1, "tokens": speed * 10,
                "elapsed": 10.0, "tok_per_sec": speed, "error": "", "workspace": root}

    monkeypatch.setattr(leaderboard, "run_model", fake_run_model)
    c = srv.Console(specs_dir="specs", workspaces_dir=str(tmp_path))
    state = c.start_leaderboard({"base_url": "http://localhost:11434/v1",
                                 "models": ["qwen", "glm-4.6"]})
    assert state["status"] in ("running", "done") and state["total"] == 2
    for _ in range(200):
        if c.leaderboard["status"] == "done":
            break
        time.sleep(0.02)
    assert c.leaderboard["status"] == "done"
    ranked = c.leaderboard["results"]
    assert [r["model"] for r in ranked] == ["glm-4.6", "qwen"]   # faster first
    assert ranked[0]["rank"] == 1


def test_leaderboard_requires_base_url(tmp_path):
    from harness import server as srv
    c = srv.Console(specs_dir="specs", workspaces_dir=str(tmp_path))
    assert "error" in c.start_leaderboard({})
