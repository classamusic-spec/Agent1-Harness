"""Tests for the build timeline: structured events + human summary + report file."""

from __future__ import annotations

from harness import timeline
from harness.timeline import Timeline


def _sample() -> Timeline:
    tl = Timeline()
    tl.round(1, ["typecheck", "tests"], tokens=12000)
    tl.event("auto-install", packages=["axios"], ok=True)
    tl.round(2, ["tests"], tokens=20000)
    tl.event("repair", n=1)
    tl.round(3, [], tokens=31000)
    tl.finish(True, "verified", tokens=31000, elapsed=95.0)
    return tl


def test_summary_reads_like_a_story():
    s = _sample().summary()
    assert "R1 2 failed (typecheck, tests)" in s
    assert "auto-install axios" in s
    assert "repair 1" in s
    assert "R3 green" in s
    assert "✓ verified" in s and "31,000 tok" in s


def test_events_are_structured_and_ordered():
    ev = _sample().events
    assert [e["kind"] for e in ev] == \
        ["verify", "auto-install", "verify", "repair", "verify", "finish"]
    assert ev[0]["failures"] == ["typecheck", "tests"]
    assert ev[-1]["ok"] is True and ev[-1]["reason"] == "verified"
    assert all("at" in e for e in ev)          # relative timestamps


def test_write_and_load_report(tmp_path):
    tl = _sample()
    tl.write(str(tmp_path))
    data = timeline.load(str(tmp_path))
    assert data["summary"] == tl.summary()
    assert len(data["events"]) == 6


def test_load_missing_or_corrupt(tmp_path):
    assert timeline.load(str(tmp_path)) == {}
    d = tmp_path / ".studio"; d.mkdir()
    (d / "report.json").write_text("{broken")
    assert timeline.load(str(tmp_path)) == {}


def test_escalation_and_review_rendering():
    tl = Timeline()
    tl.round(1, ["tests"], tokens=100)
    tl.event("escalation", target="Claude Code CLI (sonnet)")
    tl.event("review", approved=False)
    tl.event("security", blockers=2)
    tl.finish(False, "stalled", tokens=100, elapsed=10)
    s = tl.summary()
    assert "escalate → Claude Code CLI (sonnet)" in s
    assert "review rejected" in s and "security 2 blocker(s)" in s and "✗ stalled" in s


def test_build_writes_report_via_fake_loop(tmp_path):
    """End-to-end through build(): the report lands in .studio/report.json."""
    import asyncio
    from tests.test_loop import FakeEngine
    from harness.agent import build
    from harness.config import EngineConfig, HarnessConfig
    from harness.spec import Spec
    from harness.verifier import Check

    ws = str(tmp_path / "ws")
    spec = Spec(name="t", description="d", kind="cli",
                checks=[Check(name="ok", command="true")])
    cfg = HarnessConfig(workspace=ws, engine=EngineConfig(provider="local", model="fake"))
    eng = FakeEngine(ws, [lambda w, p: "built"])
    result = asyncio.run(build(spec, cfg, echo=False,
                               builder_factory=lambda s, c: eng))
    assert result.ok
    assert result.timeline and result.timeline[-1]["kind"] == "finish"
    data = timeline.load(ws)
    assert "R1 green" in data["summary"]
