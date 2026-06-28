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

    def __init__(self, workspace, actions, total_tokens=0):
        self.workspace = workspace
        self.actions = list(actions)
        self.total_tokens = total_tokens

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


def test_plan_approval_rejected_stops(tmp_path):
    from harness.approval import CallbackApproval
    spec = _spec()
    cfg = _config(tmp_path / "ws", approve_plan=True)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("never")])
    gate = CallbackApproval(lambda k, p: k != "plan")  # reject plan only
    result = asyncio.run(build(spec, cfg, echo=False,
                              builder_factory=lambda s, c: builder, approval=gate))
    assert not result.ok and result.stop_reason == "plan-rejected"


def test_build_approval_rejected(tmp_path):
    from harness.approval import CallbackApproval
    spec = _spec()
    cfg = _config(tmp_path / "ws", approve_build=True)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")])
    gate = CallbackApproval(lambda k, p: False)  # reject the finished build
    result = asyncio.run(build(spec, cfg, echo=False,
                              builder_factory=lambda s, c: builder, approval=gate))
    assert not result.ok and result.stop_reason == "build-rejected"


def test_build_approval_feedback_then_accept(tmp_path):
    from harness.approval import CallbackApproval, Decision
    spec = _spec()
    cfg = _config(tmp_path / "ws", approve_build=True, max_repairs=3)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("v1"), _writer("v2")])
    calls = {"n": 0}

    def gate_fn(kind, payload):
        calls["n"] += 1
        # First sign-off: reject with feedback; second: approve.
        return Decision(False, "make the header bigger") if calls["n"] == 1 else Decision(True)

    result = asyncio.run(build(spec, cfg, echo=False,
                              builder_factory=lambda s, c: builder,
                              approval=CallbackApproval(gate_fn)))
    assert result.ok and result.stop_reason == "verified"
    assert result.rounds == 2  # rejection fed back as a repair, then accepted
    assert not builder.actions  # both builder turns consumed (initial + feedback)


def test_build_approval_accepted(tmp_path):
    from harness.approval import CallbackApproval
    spec = _spec()
    cfg = _config(tmp_path / "ws", approve_build=True)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")])
    gate = CallbackApproval(lambda k, p: True)
    result = asyncio.run(build(spec, cfg, echo=False,
                              builder_factory=lambda s, c: builder, approval=gate))
    assert result.ok and result.stop_reason == "verified"


def test_token_budget_stops(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, max_tokens_budget=10, max_escalations=0)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop], total_tokens=50)
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok and result.stop_reason == "token-budget"


def test_on_progress_callback(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws")
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")], total_tokens=10)
    seen = []
    asyncio.run(build(spec, cfg, echo=False,
                      builder_factory=lambda s, c: builder, on_progress=lambda p: seen.append(p)))
    assert seen and "tokens" in seen[0] and "elapsed" in seen[0]
    assert seen[-1]["tokens"] == 10


def test_checkpoint_written_on_build(tmp_path):
    from harness.checkpoint import load_checkpoint
    cp = str(tmp_path / "cp.json")
    spec = _spec()
    cfg = _config(tmp_path / "ws", checkpoint_path=cp)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.ok
    saved = load_checkpoint(cp)
    assert saved.status == "completed" and saved.stop_reason == "verified"


def test_resume_continues_to_green(tmp_path):
    from harness.agent import resume
    from harness.checkpoint import load_checkpoint
    cp = str(tmp_path / "cp.json")
    ws = str(tmp_path / "ws")
    spec = _spec()

    # First run never writes the file -> fails, leaving a 'failed' checkpoint.
    cfg = _config(tmp_path / "ws", max_repairs=0, max_escalations=0, checkpoint_path=cp)
    first = asyncio.run(build(spec, cfg, echo=False,
                             builder_factory=lambda s, c: FakeEngine(ws, [_noop])))
    assert not first.ok and load_checkpoint(cp).status == "failed"

    # Resume: a fresh builder writes the file against the existing workspace -> green.
    second = asyncio.run(resume(cp, echo=False,
                               builder_factory=lambda s, c: FakeEngine(c.workspace, [_writer("done")])))
    assert second.ok and second.stop_reason == "verified"
    assert load_checkpoint(cp).status == "completed"
    assert load_checkpoint(cp).sessions == 2  # resumed once


def test_cancel_mid_run(tmp_path):
    from harness.control import BuildControl
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, max_escalations=0)
    builder = FakeEngine(str(tmp_path / "ws"), [_noop, _noop, _noop])
    ctrl = BuildControl()
    # Cancel as soon as the first round completes.
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder,
                              control=ctrl, on_progress=lambda p: ctrl.cancel()))
    assert not result.ok and result.stop_reason == "cancelled"


def test_pause_is_resumable(tmp_path):
    from harness.agent import resume
    from harness.control import BuildControl
    cp = str(tmp_path / "cp.json")
    ws = str(tmp_path / "ws")
    spec = _spec()
    cfg = _config(tmp_path / "ws", max_repairs=5, max_escalations=0, checkpoint_path=cp)
    builder = FakeEngine(ws, [_noop, _noop, _noop])
    ctrl = BuildControl()
    paused = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder,
                              control=ctrl, on_progress=lambda p: ctrl.pause()))
    assert not paused.ok and paused.stop_reason == "paused"

    # Resume and finish it.
    done = asyncio.run(resume(cp, echo=False,
                             builder_factory=lambda s, c: FakeEngine(c.workspace, [_writer("ok")])))
    assert done.ok and done.stop_reason == "verified"


def test_telemetry_reported(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws")
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("ok")], total_tokens=123)
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.tokens_used == 123
    assert result.elapsed_seconds >= 0


def test_reviewer_panel_rejects_then_approves(tmp_path):
    spec = _spec()
    cfg = _config(tmp_path / "ws", review_panel=["quality", "bugs"], max_repairs=3)
    builder = FakeEngine(str(tmp_path / "ws"), [_writer("v1"), _writer("v2")])
    state = {"n": 0}
    block = '{"approved":false,"findings":[{"severity":"blocker","title":"x"}]}'
    ok = '{"approved":true,"findings":[]}'

    def reviewer_factory(s, c):
        state["n"] += 1
        text = block if state["n"] <= 2 else ok  # round 1 (2 reviewers) blocks; round 2 passes
        return FakeEngine(c.workspace, [lambda w, p, t=text: t])

    result = asyncio.run(build(
        spec, cfg, echo=False,
        builder_factory=lambda s, c: builder,
        reviewer_factory=reviewer_factory,
    ))
    assert result.ok and result.stop_reason == "approved"
    assert result.rounds == 2
    assert state["n"] == 4  # 2 reviewers x 2 rounds


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


def test_spec_scaffold_materializes_and_gates(tmp_path):
    """A spec's `scaffold:` field (not just the --scaffold flag) must materialize
    the base and drive the gate. The static scaffold supplies index.html + checks,
    so a no-op builder still goes green."""
    ws = tmp_path / "ws"
    spec = Spec(name="demo", description="d", kind="frontend", language="javascript",
                scaffold="static")
    cfg = _config(ws)  # config.scaffold is None — the scaffold comes from the spec
    builder = FakeEngine(str(ws), [_noop, _noop, _noop, _noop])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert result.ok, result.stop_reason
    assert (Path(ws) / "index.html").is_file()  # scaffold was applied from spec.scaffold


def test_spec_checks_override_scaffold_checks(tmp_path):
    """When the spec carries its own checks, they win over the scaffold's defaults
    (a transforming build can change the API the scaffold shipped checks for)."""
    ws = tmp_path / "ws"
    spec = Spec(name="demo", description="d", kind="frontend", language="javascript",
                scaffold="static",
                checks=[Check(name="custom", command="test -f does_not_exist")])
    cfg = _config(ws, max_repairs=0)
    builder = FakeEngine(str(ws), [_noop])
    result = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not result.ok
    names = {r.name for r in result.report.results}
    assert names == {"custom"}  # the scaffold's own checks were NOT forced
    assert (Path(ws) / "index.html").is_file()  # files still materialized from the scaffold


def test_security_gate_blocks_then_repairs(tmp_path):
    """A hardcoded secret passes the functional checks but the deterministic
    security gate blocks it; a repair removes it and the build is accepted."""
    ws = tmp_path / "ws"
    spec = Spec(name="demo", description="d", kind="cli", language="python",
                checks=[Check(name="exists", command="test -f app.py")])
    cfg = _config(ws, max_repairs=2)
    cfg = __import__("dataclasses").replace(cfg, security_scan=True)

    def bad(w, p):
        Path(w, "app.py").write_text('api_key = "sk-live-abcd1234efgh5678"\n')
        return "built"

    def good(w, p):
        Path(w, "app.py").write_text('import os\napi_key = os.environ["API_KEY"]\n')
        return "fixed"

    builder = FakeEngine(str(ws), [bad, good])
    res = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert res.ok and res.stop_reason == "verified"
    assert "os.environ" in Path(ws, "app.py").read_text()


def test_security_gate_gives_up_when_unfixed(tmp_path):
    ws = tmp_path / "ws"
    spec = Spec(name="demo", description="d", kind="cli", language="python",
                checks=[Check(name="exists", command="test -f app.py")])
    cfg = __import__("dataclasses").replace(_config(ws, max_repairs=1), security_scan=True)

    def bad(w, p):
        Path(w, "app.py").write_text('token = "AKIAIOSFODNN7EXAMPLE"\n')
        return "built"

    builder = FakeEngine(str(ws), [bad, bad])
    res = asyncio.run(build(spec, cfg, echo=False, builder_factory=lambda s, c: builder))
    assert not res.ok and res.stop_reason == "security-blocked"
