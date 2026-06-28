"""Tests for multi-agent decomposition: role split, ownership merge, and the
build_multi orchestration (roles build in isolation, merge by ownership, then an
integration gate). The engine is faked; the merge/isolation plumbing is real."""

from __future__ import annotations

import asyncio
import os

import harness.agent as agent
import harness.multiagent as ma
from harness.agent import BuildResult
from harness.config import HarnessConfig
from harness.multiagent import (
    build_multi,
    default_roles,
    merge_owned,
    role_description,
)
from harness.spec import Spec


def test_default_roles_split_by_kind():
    assert [r.name for r in default_roles(Spec(name="a", description="d", kind="fullstack"))] == \
        ["backend", "frontend"]
    assert [r.name for r in default_roles(Spec(name="a", description="d", kind="frontend"))] == \
        ["frontend"]
    assert [r.name for r in default_roles(Spec(name="a", description="d", kind="api"))] == \
        ["backend"]


def test_role_description_states_ownership_and_contract():
    roles = default_roles(Spec(name="a", description="d", kind="fullstack"))
    backend = roles[0]
    body = role_description(Spec(name="a", description="build it", kind="fullstack"),
                            backend, "CONTRACT TEXT", roles)
    assert "build it" in body and "CONTRACT TEXT" in body
    assert "YOUR ROLE: backend" in body
    assert "server.py" in body            # owns
    assert "public" in body               # foreign (do-not-touch) path mentioned


def test_merge_owned_takes_only_owned_paths(tmp_path):
    base = tmp_path / "base"; base.mkdir()
    role = tmp_path / "role"; (role / "public").mkdir(parents=True)
    (role / "server.py").write_text("# server")
    (role / "public" / "evil.js").write_text("// not mine")  # not owned by backend
    merged = merge_owned(str(base), str(role), ["server.py", "migrations"])
    assert merged == ["server.py"]
    assert (base / "server.py").is_file()
    assert not (base / "public").exists()  # unowned path discarded


def _fake_build_factory(integration_ran):
    async def fake_build(spec, config, **kw):
        ws = config.workspace
        os.makedirs(ws, exist_ok=True)
        name = spec.name
        if "[backend]" in name:
            open(os.path.join(ws, "server.py"), "w").write("# server")
            os.makedirs(os.path.join(ws, "migrations"), exist_ok=True)
            open(os.path.join(ws, "migrations", "001.sql"), "w").write("-- m")
            os.makedirs(os.path.join(ws, "public"), exist_ok=True)  # stray (frontend-owned)
            open(os.path.join(ws, "public", "evil.js"), "w").write("// discard me")
        elif "[frontend]" in name:
            os.makedirs(os.path.join(ws, "public"), exist_ok=True)
            open(os.path.join(ws, "public", "index.html"), "w").write("<h1>hi</h1>")
        elif "[integration]" in name:
            integration_ran.append(ws)
        return BuildResult(ok=True, rounds=1, stop_reason="verified", tokens_used=10, workspace=ws)
    return fake_build


def test_build_multi_parallel_merges_and_integrates(tmp_path, monkeypatch):
    integration_ran: list[str] = []

    async def fake_contract(spec, config, roles, *, echo=True, scaffold_note=""):
        return "GET /api/health -> 200"

    monkeypatch.setattr(ma, "make_contract", fake_contract)
    monkeypatch.setattr(agent, "build", _fake_build_factory(integration_ran))

    ws = tmp_path / "app"
    cfg = HarnessConfig(workspace=str(ws), multi=True, multi_parallel=True)
    res = asyncio.run(build_multi(
        Spec(name="app", description="d", kind="fullstack"), cfg, echo=False))

    assert res.ok and res.stop_reason == "verified"
    assert res.tokens_used == 30  # backend + frontend + integration, 10 each
    assert [o["title"] for o in res.roles] == ["backend", "frontend", "integration"]
    # merge took owned paths from each role's isolated worktree
    assert (ws / "server.py").is_file()
    assert (ws / "migrations" / "001.sql").is_file()
    assert (ws / "public" / "index.html").is_file()
    # the backend's stray frontend-owned file was discarded at merge
    assert not (ws / "public" / "evil.js").exists()
    # integration ran on the merged base workspace, and role worktrees were cleaned up
    assert integration_ran == [str(ws)]
    parent = tmp_path
    assert not any(p.name.startswith("app.role-") for p in parent.iterdir())


def test_build_multi_sequential_uses_shared_workspace(tmp_path, monkeypatch):
    integration_ran: list[str] = []

    async def fake_contract(spec, config, roles, *, echo=True, scaffold_note=""):
        return ""

    monkeypatch.setattr(ma, "make_contract", fake_contract)
    monkeypatch.setattr(agent, "build", _fake_build_factory(integration_ran))

    ws = tmp_path / "app"
    cfg = HarnessConfig(workspace=str(ws), multi=True, multi_parallel=False)
    res = asyncio.run(build_multi(
        Spec(name="app", description="d", kind="fullstack"), cfg, echo=False))
    assert res.ok
    assert (ws / "server.py").is_file() and (ws / "public" / "index.html").is_file()
    # sequential builds share the workspace directly (no .role- copies)
    assert not any(p.name.startswith("app.role-") for p in tmp_path.iterdir())


def test_build_multi_stops_when_a_role_fails(tmp_path, monkeypatch):
    integration_ran: list[str] = []

    async def fake_contract(spec, config, roles, *, echo=True, scaffold_note=""):
        return ""

    async def fake_build(spec, config, **kw):
        ok = "[backend]" not in spec.name  # backend fails
        if "[integration]" in spec.name:
            integration_ran.append(config.workspace)
        return BuildResult(ok=ok, rounds=1, stop_reason="verified" if ok else "verify-failed",
                           tokens_used=5, workspace=config.workspace)

    monkeypatch.setattr(ma, "make_contract", fake_contract)
    monkeypatch.setattr(agent, "build", fake_build)

    ws = tmp_path / "app"
    cfg = HarnessConfig(workspace=str(ws), multi=True, multi_parallel=True)
    res = asyncio.run(build_multi(
        Spec(name="app", description="d", kind="fullstack"), cfg, echo=False))
    assert not res.ok
    assert res.stop_reason == "role-failed: backend"
    assert integration_ran == []  # integration skipped after a role failure


def test_build_dispatches_to_multi(tmp_path, monkeypatch):
    called = {}

    async def fake_multi(spec, config, **kw):
        called["yes"] = True
        return BuildResult(ok=True, rounds=1, stop_reason="verified", workspace=config.workspace)

    monkeypatch.setattr("harness.multiagent.build_multi", fake_multi)
    cfg = HarnessConfig(workspace=str(tmp_path / "ws"), multi=True)
    res = asyncio.run(agent.build(Spec(name="a", description="d", kind="fullstack"), cfg, echo=False))
    assert res.ok and called.get("yes")
