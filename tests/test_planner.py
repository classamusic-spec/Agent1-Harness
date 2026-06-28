"""Tests for the milestone planner: parsing, the implement prompt, and the
_build_planned orchestration (each milestone is its own build, in order)."""

from __future__ import annotations

import asyncio

import harness.agent as agent
from harness import planner
from harness.agent import BuildResult, _build_planned
from harness.config import HarnessConfig
from harness.planner import Milestone, milestone_description, parse_plan
from harness.spec import Spec


def test_parse_plan_reads_milestones():
    text = ('noise before {"milestones": ['
            '{"title": "Schema", "goal": "define the data model"},'
            '{"title": "API", "goal": "CRUD endpoints", "checks": ['
            '{"name": "health", "command": "curl $APP_URL/api/health", "needs_server": true}]}'
            ']} trailing')
    ms = parse_plan(text, 6)
    assert [m.title for m in ms] == ["Schema", "API"]
    assert ms[1].checks[0]["needs_server"] is True


def test_parse_plan_caps_and_handles_garbage():
    assert parse_plan("not json", 6) == []
    many = '{"milestones": [' + ",".join('{"title":"m%d","goal":"g"}' % i for i in range(10)) + "]}"
    assert len(parse_plan(many, 4)) == 4


def test_milestone_description_marks_current_and_includes_spec():
    spec = Spec(name="app", description="A todo app", kind="fullstack")
    ms = [Milestone("Schema", "model"), Milestone("API", "endpoints"), Milestone("UI", "frontend")]
    body = milestone_description(spec, ms, 1)
    assert "A todo app" in body
    assert "CURRENT MILESTONE (2/3): API" in body
    assert "→ 2. API" in body and "✓ 1. Schema" in body


def test_build_planned_runs_each_milestone_in_order(tmp_path, monkeypatch):
    calls = []

    async def fake_plan(spec, config, *, echo=True):
        return [Milestone("Schema", "model"), Milestone("API", "endpoints"), Milestone("UI", "ui")]

    async def fake_build(spec, config, **kw):
        calls.append(spec.description.split("CURRENT MILESTONE")[1].split(":")[1].strip().split("\n")[0])
        return BuildResult(ok=True, rounds=1, stop_reason="verified", tokens_used=10,
                           workspace=config.workspace)

    monkeypatch.setattr(planner, "make_plan", fake_plan)
    monkeypatch.setattr(agent, "build", fake_build)
    cfg = HarnessConfig(workspace=str(tmp_path), plan=True)
    res = asyncio.run(_build_planned(
        Spec(name="app", description="d", kind="fullstack"), cfg, echo=False,
        builder_factory=agent._default_builder, reviewer_factory=agent._default_reviewer,
        fixer_factory=agent._default_fixer, planner_factory=agent._default_planner,
        approval=None, on_progress=None, control=None))
    assert res.ok and res.tokens_used == 30
    assert len(res.milestones) == 3 and all(m["ok"] for m in res.milestones)
    assert calls == ["Schema", "API", "UI"]


def test_build_planned_stops_on_failed_milestone(tmp_path, monkeypatch):
    async def fake_plan(spec, config, *, echo=True):
        return [Milestone("A", "a"), Milestone("B", "b"), Milestone("C", "c")]

    n = {"i": 0}

    async def fake_build(spec, config, **kw):
        n["i"] += 1
        ok = n["i"] != 2  # second milestone fails
        return BuildResult(ok=ok, rounds=1, stop_reason="verified" if ok else "verify-failed",
                           tokens_used=5, workspace=config.workspace)

    monkeypatch.setattr(planner, "make_plan", fake_plan)
    monkeypatch.setattr(agent, "build", fake_build)
    res = asyncio.run(_build_planned(
        Spec(name="app", description="d"), HarnessConfig(workspace=str(tmp_path), plan=True),
        echo=False, builder_factory=agent._default_builder, reviewer_factory=agent._default_reviewer,
        fixer_factory=agent._default_fixer, planner_factory=agent._default_planner,
        approval=None, on_progress=None, control=None))
    assert not res.ok
    assert n["i"] == 2  # stopped after the failing milestone (didn't run C)
    assert "milestone-failed: B" in res.stop_reason
