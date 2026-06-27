"""The orchestrator: a self-improving, engine-agnostic build loop.

One round of the loop:

    implement/repair  ->  VERIFY (deterministic gate)  ->  REVIEW (agent gate)

The build succeeds only when verification passes *and* the reviewer/sentry gate
approves (when enabled). Convergence is guarded: the loop bails early if repairs
stop making progress (stall) or a wall-clock budget is exceeded (timeout), so it
never burns the full budget on a wedged build. Lessons from failures — and, when
enabled, a model-driven reflection — are written to memory and injected into
future builds. Everything runs inside an isolated workspace.

The loop is engine-agnostic (Claude or local LLM) and accepts factory overrides
so it can be driven by a fake engine in tests.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable

from harness.config import HarnessConfig
from harness.engines import Engine, make_engine
from harness.isolation import workspace_session
from harness.memory import LessonStore, lessons_from_findings, lessons_from_report
from harness.personas import reviewer_system
from harness.prompts import build_prompt, repair_prompt, review_repair_prompt, with_lessons
from harness.reflect import reflect
from harness.review import ReviewVerdict, run_review
from harness.spec import Spec
from harness.verifier import VerificationReport, run_suite

BuilderFactory = Callable[[Spec, HarnessConfig], Engine]
ReviewerFactory = Callable[[Spec, HarnessConfig], Engine]


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
    progress: list[int] = field(default_factory=list)  # failing-check count per round
    transcript: list[str] = field(default_factory=list)


def _default_builder(spec: Spec, config: HarnessConfig) -> Engine:
    return make_engine(spec, config)


def _default_reviewer(spec: Spec, config: HarnessConfig) -> Engine:
    rconfig = dataclasses.replace(config, engine=config.reviewer_engine())
    return make_engine(spec, rconfig, system_prompt_override=reviewer_system())


def _banner(text: str) -> str:
    return f"\n{'=' * 8} {text} {'=' * 8}"


def _signature(report: VerificationReport) -> frozenset:
    """A comparable fingerprint of a failing round, to detect no-progress."""
    return frozenset((r.name, r.returncode) for r in report.failures)


def _expired(start: float, deadline: float | None) -> bool:
    return deadline is not None and (time.monotonic() - start) >= deadline


async def build(
    spec: Spec,
    config: HarnessConfig,
    *,
    echo: bool = True,
    builder_factory: BuilderFactory = _default_builder,
    reviewer_factory: ReviewerFactory = _default_reviewer,
) -> BuildResult:
    """Run the full self-improving build loop for a spec."""
    store = LessonStore(config.memory_path) if config.learn and config.memory_path else None

    with workspace_session(
        config.isolation,
        config.workspace,
        base_repo=config.base_repo,
        branch=f"appbuilder/{spec.name}",
        keep=config.keep_workspace,
    ) as effective_ws:
        run_config = dataclasses.replace(config, workspace=effective_ws)
        for c in spec.checks:
            c.cwd = effective_ws

        lessons_text = store.render(spec.kind, spec.language) if store else ""
        transcript: list[str] = []
        progress: list[int] = []
        first_report: VerificationReport | None = None
        start = time.monotonic()
        builder = builder_factory(spec, run_config)

        async with builder:
            transcript.append(await builder.send(with_lessons(build_prompt(spec), lessons_text), echo=echo))

            if not spec.has_verification:
                return BuildResult(True, 1, "no-verification", workspace=effective_ws, transcript=transcript)

            async def finish(ok, reason, *, report=None, verdict=None, findings=None):
                learned = await _finalize(store, spec, config, builder, report=report,
                                          findings=findings, echo=echo)
                return BuildResult(ok, len(progress), reason, report, verdict, effective_ws,
                                   learned, progress, transcript)

            verify_repairs = config.max_repairs
            review_repairs = config.max_repairs
            review_attempt = 0
            prev_sig: frozenset | None = None
            stall = 0

            while True:
                report = run_suite(spec.checks, stop_on_failure=config.stop_on_failure)
                progress.append(len(report.failures))
                if first_report is None and not report.ok:
                    first_report = report
                if echo:
                    print(_banner(f"verification (round {len(progress)})"), flush=True)
                    print(report.to_feedback() or "(no checks)", flush=True)

                if not report.ok:
                    sig = _signature(report)
                    stall = stall + 1 if sig == prev_sig else 1
                    prev_sig = sig
                    if stall >= config.stall_limit:
                        if echo:
                            print(f"[loop] no progress for {stall} rounds — stopping (stalled)", flush=True)
                        return await finish(False, "stalled", report=report)
                    if verify_repairs <= 0:
                        return await finish(False, "verify-failed", report=report)
                    if _expired(start, config.deadline_seconds):
                        return await finish(False, "timeout", report=report)
                    verify_repairs -= 1
                    n = config.max_repairs - verify_repairs
                    transcript.append(await builder.send(repair_prompt(report, n, config.max_repairs), echo=echo))
                    continue

                # Verification passed. Apply the reviewer/sentry gate if enabled.
                if not config.enable_review:
                    return await finish(True, "verified", report=first_report)

                if echo:
                    print(_banner(f"review: {config.review_focus} (round {len(progress)})"), flush=True)
                reviewer = reviewer_factory(spec, run_config)
                async with reviewer:
                    verdict = await run_review(reviewer, config.review_focus, echo=echo)
                if echo:
                    print(f"review approved={verdict.approved} | {verdict.summary}", flush=True)

                if verdict.approved:
                    return await finish(True, "approved", report=first_report, verdict=verdict)
                if review_repairs <= 0:
                    return await finish(False, "review-rejected", verdict=verdict, findings=verdict.findings)
                if _expired(start, config.deadline_seconds):
                    return await finish(False, "timeout", verdict=verdict, findings=verdict.findings)
                review_repairs -= 1
                review_attempt += 1
                transcript.append(
                    await builder.send(review_repair_prompt(verdict, review_attempt, config.max_repairs), echo=echo)
                )


async def _finalize(store, spec, config, builder, *, report=None, findings=None, echo=True) -> int:
    """Record lessons learned this build (mechanical + optional reflection)."""
    if store is None:
        return 0
    lessons = []
    if report is not None:
        lessons += lessons_from_report(report, spec)
    if findings:
        lessons += lessons_from_findings(findings, spec)
    # Reflect only when there was something to learn from (a failure or rejection).
    if config.reflect and (report is not None or findings):
        reflected = await reflect(builder, spec, echo=echo)
        lessons = reflected + lessons  # richer lessons first; dedupe happens on write
    return store.add_many(lessons)
