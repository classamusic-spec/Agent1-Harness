"""The orchestrator: a self-improving, engine-agnostic build loop.

One round of the loop:

    implement/repair  ->  VERIFY (deterministic gate)  ->  REVIEW (agent gate)

The build succeeds only when verification passes *and* the reviewer/sentry gate
approves (when enabled). Lessons from failures and reviewer findings are written
to a memory store and injected into future builds, so the agent learns from its
own mistakes. The whole thing runs inside an isolated workspace.

The loop is engine-agnostic (Claude or local LLM) and accepts factory overrides
so it can be driven by a fake engine in tests.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Callable

from harness.config import HarnessConfig
from harness.engines import Engine, make_engine
from harness.isolation import workspace_session
from harness.memory import LessonStore, lessons_from_findings, lessons_from_report
from harness.personas import reviewer_system
from harness.prompts import build_prompt, repair_prompt, review_repair_prompt, with_lessons
from harness.review import ReviewVerdict, run_review
from harness.spec import Spec
from harness.verifier import VerificationReport, run_suite

BuilderFactory = Callable[[Spec, HarnessConfig], Engine]
ReviewerFactory = Callable[[Spec, HarnessConfig], Engine]


@dataclass
class BuildResult:
    ok: bool
    rounds: int
    report: VerificationReport | None = None
    verdict: ReviewVerdict | None = None
    workspace: str = ""
    lessons_learned: int = 0
    transcript: list[str] = field(default_factory=list)


def _default_builder(spec: Spec, config: HarnessConfig) -> Engine:
    return make_engine(spec, config)


def _default_reviewer(spec: Spec, config: HarnessConfig) -> Engine:
    rconfig = dataclasses.replace(config, engine=config.reviewer_engine())
    return make_engine(spec, rconfig, system_prompt_override=reviewer_system())


def _banner(text: str) -> str:
    return f"\n{'=' * 8} {text} {'=' * 8}"


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
        # Point engines and checks at the effective (possibly isolated) workspace.
        run_config = dataclasses.replace(config, workspace=effective_ws)
        for c in spec.checks:
            c.cwd = effective_ws

        # Inject relevant lessons from past builds.
        lessons_text = store.render(spec.kind, spec.language) if store else ""

        transcript: list[str] = []
        first_report: VerificationReport | None = None
        builder = builder_factory(spec, run_config)

        async with builder:
            transcript.append(await builder.send(with_lessons(build_prompt(spec), lessons_text), echo=echo))

            if not spec.has_verification:
                return BuildResult(ok=True, rounds=1, workspace=effective_ws, transcript=transcript)

            verify_repairs_left = config.max_repairs
            review_repairs_left = config.max_repairs
            review_attempt = 0
            rounds = 0

            while True:
                rounds += 1
                report = run_suite(spec.checks, stop_on_failure=config.stop_on_failure)
                if first_report is None:
                    first_report = report
                if echo:
                    print(_banner(f"verification (round {rounds})"), flush=True)
                    print(report.to_feedback() or "(no checks)", flush=True)

                if not report.ok:
                    if verify_repairs_left <= 0:
                        learned = _record(store, spec, report=report)
                        return BuildResult(False, rounds, report, None, effective_ws, learned, transcript)
                    verify_repairs_left -= 1
                    n = config.max_repairs - verify_repairs_left
                    transcript.append(
                        await builder.send(repair_prompt(report, n, config.max_repairs), echo=echo)
                    )
                    continue

                # Verification passed. Apply the reviewer/sentry gate if enabled.
                if not config.enable_review:
                    learned = _record(store, spec, report=first_report if rounds > 1 else None)
                    return BuildResult(True, rounds, report, None, effective_ws, learned, transcript)

                if echo:
                    print(_banner(f"review: {config.review_focus} (round {rounds})"), flush=True)
                reviewer = reviewer_factory(spec, run_config)
                async with reviewer:
                    verdict = await run_review(reviewer, config.review_focus, echo=echo)
                if echo:
                    print(f"review approved={verdict.approved} | {verdict.summary}", flush=True)

                if verdict.approved:
                    learned = _record(store, spec, report=first_report if rounds > 1 else None)
                    return BuildResult(True, rounds, report, verdict, effective_ws, learned, transcript)

                if review_repairs_left <= 0:
                    learned = _record(store, spec, report=None, findings=verdict.findings)
                    return BuildResult(False, rounds, report, verdict, effective_ws, learned, transcript)
                review_repairs_left -= 1
                review_attempt += 1
                transcript.append(
                    await builder.send(
                        review_repair_prompt(verdict, review_attempt, config.max_repairs), echo=echo
                    )
                )


def _record(store, spec, *, report=None, findings=None) -> int:
    """Persist lessons learned this build. Returns the number written."""
    if store is None:
        return 0
    lessons = []
    if report is not None:
        lessons += lessons_from_report(report, spec)
    if findings:
        lessons += lessons_from_findings(findings, spec)
    return store.add_many(lessons)
