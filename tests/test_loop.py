"""Integration tests for the self-improving build loop, using a fake engine.

These exercise the real verifier, memory, isolation, and review-gate plumbing —
only the model is faked. No network, no API key.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.agent import build
from harness.config import EngineConfig, HarnessConfig
from harness.engines.base import Engine
from harness.memory import LessonStore
from harness.spec import Spec
from harness.verifier import Check


class FakeEngine(Engine):
    """Consumes a list of actions; each action(workspace, prompt) -> str and may
    mutate the workspace to simulate the model writing/fixing code."""

    def __init__(self, workspace, actions):
        self.workspace = workspace
        self.actions = list(actions)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, prompt, *, echo=True):
        if self.actions:
            return self.actions.pop(0)(self.workspace, prompt)
        return ""


def _spec(**kw):
    return Spec(
        name="demo", description="d", kind="cli", language="python",
        checks=[Check(name="exists", command="test -f app.txt")], **kw
    )


def _config(workspace, **kw):
    return HarnessConfig(workspace=str(workspace), engine=EngineConfig(provider="local", model="fake"), **kw)


def _writer(content):
    def act(ws, prompt):
        Path(ws, "app.txt").write_text(content)
        return "built"
    return act


def _noop(ws, prompt):
    return "(thinking)"


def test_passes_on_first_attempt(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws")
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("hello")])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.ok and result.rounds == 1


def test_repairs_then_passes(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=2)
    # First send writes nothing (verify fails), repair send writes the file.
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _writer("fixed")])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.ok and result.rounds == 2


def test_fails_when_repairs_exhausted(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=1)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop])  # never writes the file
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok


def test_review_gate_rejects_then_approves(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", enable_review=True, review_focus="bugs", max_repairs=2)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("v1"), _writer("v2")])

    verdicts = [
        '{"summary":"bug","approved":false,"findings":[{"severity":"blocker","title":"npe"}]}',
        '{"summary":"clean","approved":true,"findings":[]}',
    ]

    def reviewer_factory(s, c):
        text = verdicts.pop(0)
        return FakeEngine(c.workspace, [lambda ws, p, t=text: t])

    result = asyncio.run(build(
        spec, cfg, echo=False,
        builder_factory=lambda s, c: builder,
        reviewer_factory=reviewer_factory,
    ))
    assert result.ok
    assert result.verdict is not None and result.verdict.approved
    assert result.rounds == 2


def test_stalls_when_no_progress(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, stall_limit=2, max_escalations=0)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop, _noop])  # never fixes it
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok
    assert result.stop_reason == "stalled"
    assert result.rounds == 2  # bailed early instead of using all 5 repairs


def test_escalation_fixes_a_stall(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, stall_limit=2, max_escalations=1)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop, _noop])  # builder stays stuck
    fixer_calls = {"n": 0}

    def fixer_factory(s, c):
        fixer_calls["n"] += 1
        return FakeEngine(c.workspace, [_writer("fixed by specialist")])

    result = asyncio.run(build(
        spec, cfg, echo=False,
        builder_factory=lambda s, c: builder,
        fixer_factory=fixer_factory,
    ))
    assert result.ok and result.stop_reason == "verified"
    assert result.escalations == 1
    assert fixer_calls["n"] == 1


def test_stall_stops_when_escalations_exhausted(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, stall_limit=2, max_escalations=0)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop, _noop])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok and result.stop_reason == "stalled"
    assert result.escalations == 0


def test_timeout_budget(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, deadline_seconds=0.0)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok
    assert result.stop_reason == "timeout"


def test_progress_tracked(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=2)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _writer("ok")])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.progress == [1, 0]  # one failing check, then zero
    assert result.stop_reason == "verified"


def test_test_first_generates_then_builds_to_green(tmp_path):
    # Spec has NO verification; test-first derives it from a planner.
    spec = Spec(name="demo", description="d", kind="cli", language="python")
    cfg = _config(tmp_path / "ws", test_first=True)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")])

    plan_json = '{"checks":[{"name":"exists","command":"test -f app.txt"}]}'

    def planner_factory(s, c):
        return FakeEngine(c.workspace, [lambda ws, p: plan_json])

    result = asyncio.run(build(
        spec, cfg, echo=False,
        builder_factory=lambda s, c: builder,
        planner_factory=planner_factory,
    ))
    assert result.ok and result.stop_reason == "verified"
    assert [c.name for c in spec.checks] == ["exists"]  # suite came from the planner
    assert result.progress == [0]


def test_learning_records_lessons(tmp_path):
    mem = tmp_path / "lessons.jsonl"
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=2, learn=True, memory_path=str(mem))
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _writer("fixed")])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.ok
    assert result.lessons_learned >= 1
    store = LessonStore(str(mem))
    assert store.load()  # a lesson about the first failing 'exists' check was saved
