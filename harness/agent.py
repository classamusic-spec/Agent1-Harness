"""The orchestrator: a self-improving, engine-agnostic build loop.

One round of the loop:

    implement/repair  ->  VERIFY (deterministic gate)  ->  REVIEW (agent gate)

The build succeeds only when verification passes *and* the reviewer/sentry gate
approves (when enabled).

Convergence intelligence:
  - Diff-aware repair: each repair turn shows the model a unified diff of its
    last change plus the delta in failing checks, so it targets the regression.
  - Stall escalation: if repairs keep producing the same failures, a fresh-context
    "fixer" engine (optionally a stronger model) is brought in to try a different
    approach before the loop gives up.
  - Time budget: an optional wall-clock deadline.

Lessons (mechanical + model reflection) are written to memory and injected into
future builds. Everything runs inside an isolated workspace. The loop is
engine-agnostic and accepts factory overrides so a fake engine can drive it in
tests.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable

from harness.approval import ApprovalGate, AutoApprove
from harness.checkpoint import Checkpoint, checkpoint_exists, load_checkpoint, save_checkpoint
from harness.config import HarnessConfig, config_from_dict, config_to_dict
from harness.diffing import diff_snapshots, snapshot
from harness.engines import Engine, make_engine
from harness.isolation import workspace_session
from harness.memory import LessonStore, lessons_from_findings, lessons_from_report
from harness.personas import fixer_system, reviewer_system, testauthor_system
from harness.sandbox import build_runner
from harness.testfirst import propose_checks
from harness.prompts import (
    build_prompt,
    escalation_prompt,
    human_feedback_prompt,
    repair_prompt,
    review_repair_prompt,
    with_lessons,
)
from harness.reflect import reflect
from harness.review import ReviewVerdict, run_panel
from harness.spec import Spec, parse_spec, spec_to_dict
from harness.verifier import VerificationReport, failure_delta, run_suite

Factory = Callable[[Spec, HarnessConfig], Engine]


@dataclass
class BuildResult:
    ok: bool
    rounds: int
    stop_reason: str = ""  # verified | approved | no-verification | verify-failed
    #                        | review-rejected | stalled | timeout
    report: VerificationReport | None = None
    verdict: ReviewVerdict | None = None
    workspace: str = ""
    lessons_learned: int = 0
    escalations: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    progress: list[int] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)


def _default_builder(spec: Spec, config: HarnessConfig) -> Engine:
    return make_engine(spec, config)


def _default_reviewer(spec: Spec, config: HarnessConfig) -> Engine:
    rconfig = dataclasses.replace(config, engine=config.reviewer_engine())
    return make_engine(spec, rconfig, system_prompt_override=reviewer_system())


def _default_fixer(spec: Spec, config: HarnessConfig) -> Engine:
    fconfig = dataclasses.replace(config, engine=config.fixer_engine())
    return make_engine(spec, fconfig, system_prompt_override=fixer_system())


def _default_planner(spec: Spec, config: HarnessConfig) -> Engine:
    return make_engine(spec, config, system_prompt_override=testauthor_system())


def _banner(text: str) -> str:
    return f"\n{'=' * 8} {text} {'=' * 8}"


def _signature(report: VerificationReport) -> frozenset:
    return frozenset((r.name, r.returncode) for r in report.failures)


def _expired(start: float, deadline: float | None) -> bool:
    return deadline is not None and (time.monotonic() - start) >= deadline


async def build(
    spec: Spec,
    config: HarnessConfig,
    *,
    echo: bool = True,
    builder_factory: Factory = _default_builder,
    reviewer_factory: Factory = _default_reviewer,
    fixer_factory: Factory = _default_fixer,
    planner_factory: Factory = _default_planner,
    approval: ApprovalGate | None = None,
    on_progress=None,
    control=None,
) -> BuildResult:
    """Run the full self-improving build loop for a spec."""
    approval = approval or AutoApprove()
    store = LessonStore(config.memory_path) if config.learn and config.memory_path else None

    with workspace_session(
        config.isolation,
        config.workspace,
        base_repo=config.base_repo,
        branch=f"appbuilder/{spec.name}",
        keep=config.keep_workspace,
    ) as ws:
        run_config = dataclasses.replace(config, workspace=ws)
        runner = build_runner(run_config)
        for c in spec.checks:
            c.cwd = ws

        # Test-first: derive the verification suite from the spec before building.
        if config.test_first:
            planner = planner_factory(spec, run_config)
            async with planner:
                proposed = await propose_checks(planner, spec, echo=echo)
            if proposed:
                spec.checks = proposed
                for c in spec.checks:
                    c.cwd = ws
                if echo:
                    print(_banner("test-first: proposed verification suite"), flush=True)
                    for c in spec.checks:
                        print(f"  - {c.name}: {c.command}", flush=True)

        # Human-in-the-loop: sign off on the verification suite before building.
        if config.approve_plan and spec.has_verification:
            dec = await approval.request("plan", {"checks": [(c.name, c.command) for c in spec.checks]})
            if not dec.approved:
                if echo:
                    print(f"[approval] plan rejected: {dec.message}", flush=True)
                return BuildResult(False, 0, "plan-rejected", workspace=ws)

        lessons_text = store.render(spec.kind, spec.language) if store else ""
        transcript: list[str] = []
        progress: list[int] = []
        first_report: VerificationReport | None = None
        prev_report: VerificationReport | None = None
        prev_snap: dict | None = None
        escalations = 0
        extra_tokens = 0  # reviewer + fixer tokens (builder counted at finish)
        start = time.monotonic()

        # Resume support: carry cumulative tokens/elapsed and session count.
        base_tokens, base_elapsed, sessions = 0, 0.0, 1
        if config.checkpoint_path and checkpoint_exists(config.checkpoint_path):
            try:
                prior = load_checkpoint(config.checkpoint_path)
                base_tokens, base_elapsed, sessions = (
                    prior.tokens_used, prior.elapsed_seconds, prior.sessions + 1)
                if echo:
                    print(_banner(f"resuming build (session {sessions})"), flush=True)
            except Exception:
                pass

        builder = builder_factory(spec, run_config)

        def _diff() -> str:
            return diff_snapshots(prev_snap, cur_snap) if config.diff_aware else ""

        def _tokens() -> int:
            return base_tokens + getattr(builder, "total_tokens", 0) + extra_tokens

        def _elapsed() -> float:
            return base_elapsed + (time.monotonic() - start)

        def _write_cp(status: str, stop_reason: str = "") -> None:
            if not config.checkpoint_path:
                return
            try:
                save_checkpoint(config.checkpoint_path, Checkpoint(
                    spec=spec_to_dict(spec),
                    config=config_to_dict(dataclasses.replace(config, workspace=ws)),
                    workspace=ws, status=status, stop_reason=stop_reason,
                    rounds=len(progress), escalations=escalations,
                    tokens_used=_tokens(), elapsed_seconds=round(_elapsed(), 2),
                    sessions=sessions, progress=list(progress)))
            except Exception:
                pass

        async with builder:
            transcript.append(await builder.send(with_lessons(build_prompt(spec), lessons_text), echo=echo))
            cur_snap = snapshot(ws)

            if not spec.has_verification:
                _write_cp("completed", "no-verification")
                return BuildResult(True, 1, "no-verification", workspace=ws,
                                   tokens_used=_tokens(), elapsed_seconds=_elapsed(),
                                   transcript=transcript)

            async def finish(ok, reason, *, report=None, verdict=None, findings=None):
                learned = await _finalize(store, spec, config, builder, report=report,
                                          findings=findings, echo=echo)
                _write_cp("completed" if ok else "failed", reason)
                return BuildResult(ok, len(progress), reason, report, verdict, ws,
                                   learned, escalations, _tokens(), _elapsed(),
                                   progress, transcript)

            async def accept(reason, *, report=None, verdict=None):
                """Final human sign-off. Returns a BuildResult to stop, or None to
                continue the loop after feeding rejection feedback back as a repair."""
                nonlocal review_repairs, prev_snap, cur_snap
                if config.approve_build:
                    dec = await approval.request("build", {"workspace": ws, "reason": reason})
                    if not dec.approved:
                        if echo:
                            print(f"[approval] build rejected: {dec.message or '(no message)'}", flush=True)
                        # Feedback-driven: a message + budget left -> targeted repair, keep going.
                        if dec.message and review_repairs > 0:
                            review_repairs -= 1
                            transcript.append(await builder.send(
                                human_feedback_prompt(dec.message), echo=echo))
                            prev_snap, cur_snap = cur_snap, snapshot(ws)
                            return None
                        return await finish(False, "build-rejected", report=report, verdict=verdict)
                return await finish(True, reason, report=report, verdict=verdict)

            verify_repairs = config.max_repairs
            review_repairs = config.max_repairs
            review_attempt = 0
            prev_sig: frozenset | None = None
            stall = 0

            while True:
                if control is not None:
                    req = control.requested()
                    if req:
                        if echo:
                            print(_banner(f"{req} requested — stopping (resumable)"), flush=True)
                        return await finish(False, req, report=first_report)
                report = run_suite(spec.checks, stop_on_failure=config.stop_on_failure, runner=runner)
                progress.append(len(report.failures))
                if on_progress:
                    on_progress({"tokens": _tokens(), "elapsed": _elapsed(), "round": len(progress)})
                _write_cp("running")
                if first_report is None and not report.ok:
                    first_report = report
                if echo:
                    print(_banner(f"verification (round {len(progress)})"), flush=True)
                    print(report.to_feedback() or "(no checks)", flush=True)

                if config.max_tokens_budget and _tokens() >= config.max_tokens_budget:
                    if echo:
                        print(f"[loop] token budget reached ({config.max_tokens_budget}) — stopping", flush=True)
                    return await finish(report.ok, "token-budget", report=None if report.ok else report)

                if not report.ok:
                    sig = _signature(report)
                    stall = stall + 1 if sig == prev_sig else 1
                    prev_sig = sig

                    if stall >= config.stall_limit:
                        if escalations < config.max_escalations:
                            escalations += 1
                            if echo:
                                print(_banner(f"escalation {escalations}/{config.max_escalations}: "
                                              "fresh fixer engine"), flush=True)
                            diff, delta = _diff(), failure_delta(prev_report, report)
                            fixer = fixer_factory(spec, run_config)
                            async with fixer:
                                transcript.append(await fixer.send(
                                    escalation_prompt(report, escalations, config.max_escalations,
                                                      diff=diff, delta=delta), echo=echo))
                            extra_tokens += getattr(fixer, "total_tokens", 0)
                            prev_snap, cur_snap = cur_snap, snapshot(ws)
                            prev_report = report
                            stall, prev_sig = 0, None
                            continue
                        if echo:
                            print("[loop] stuck after escalation — stopping (stalled)", flush=True)
                        return await finish(False, "stalled", report=report)

                    if verify_repairs <= 0:
                        return await finish(False, "verify-failed", report=report)
                    if _expired(start, config.deadline_seconds):
                        return await finish(False, "timeout", report=report)

                    verify_repairs -= 1
                    n = config.max_repairs - verify_repairs
                    diff, delta = _diff(), failure_delta(prev_report, report)
                    transcript.append(await builder.send(
                        repair_prompt(report, n, config.max_repairs, diff=diff, delta=delta), echo=echo))
                    prev_snap, cur_snap = cur_snap, snapshot(ws)
                    prev_report = report
                    continue

                # Verification passed. Apply the reviewer/sentry gate if enabled.
                if not (config.enable_review or config.review_panel):
                    res = await accept("verified", report=first_report)
                    if res is not None:
                        return res
                    continue  # rejection feedback sent; re-verify next round

                focuses = config.review_panel or [config.review_focus]
                if echo:
                    print(_banner(f"review panel: {', '.join(focuses)} (round {len(progress)})"), flush=True)
                verdict, rtokens = await run_panel(
                    lambda: reviewer_factory(spec, run_config), focuses, echo=echo)
                extra_tokens += rtokens
                if echo:
                    print(f"review approved={verdict.approved} | {verdict.summary}", flush=True)

                if verdict.approved:
                    res = await accept("approved", report=first_report, verdict=verdict)
                    if res is not None:
                        return res
                    continue  # rejection feedback sent; re-verify next round
                if review_repairs <= 0:
                    return await finish(False, "review-rejected", verdict=verdict, findings=verdict.findings)
                if _expired(start, config.deadline_seconds):
                    return await finish(False, "timeout", verdict=verdict, findings=verdict.findings)
                review_repairs -= 1
                review_attempt += 1
                transcript.append(await builder.send(
                    review_repair_prompt(verdict, review_attempt, config.max_repairs), echo=echo))
                prev_snap, cur_snap = cur_snap, snapshot(ws)


async def resume(checkpoint_path: str, *, echo: bool = True, **kwargs) -> BuildResult:
    """Resume a build from a checkpoint, continuing against its existing workspace."""
    cp = load_checkpoint(checkpoint_path)
    spec = parse_spec(cp.spec)
    config = config_from_dict(cp.config)
    if not config.checkpoint_path:
        config = dataclasses.replace(config, checkpoint_path=checkpoint_path)
    return await build(spec, config, echo=echo, **kwargs)


async def _finalize(store, spec, config, builder, *, report=None, findings=None, echo=True) -> int:
    if store is None:
        return 0
    lessons = []
    if report is not None:
        lessons += lessons_from_report(report, spec)
    if findings:
        lessons += lessons_from_findings(findings, spec)
    if config.reflect and (report is not None or findings):
        lessons = (await reflect(builder, spec, echo=echo)) + lessons
    return store.add_many(lessons)
