"""The orchestrator: an engine-agnostic plan -> implement -> verify -> repair loop.

This module owns the deterministic control flow. It picks an engine (Claude or a
local LLM), then drives the same loop for either: the model writes code, and the
verification gate — not the model — decides when the build is done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harness.config import HarnessConfig
from harness.engines import make_engine
from harness.prompts import build_prompt, repair_prompt
from harness.spec import Spec
from harness.verifier import VerificationReport, run_suite


@dataclass
class BuildResult:
    ok: bool
    attempts: int
    report: VerificationReport | None
    transcript: list[str] = field(default_factory=list)


def _banner(text: str) -> str:
    return f"\n{'=' * 8} {text} {'=' * 8}"


async def build(spec: Spec, config: HarnessConfig, *, echo: bool = True) -> BuildResult:
    """Run the full build-and-verify loop for a spec, on the configured engine."""
    Path(config.workspace).mkdir(parents=True, exist_ok=True)

    transcript: list[str] = []
    engine = make_engine(spec, config)

    async with engine:
        # Initial build turn.
        transcript.append(await engine.send(build_prompt(spec), echo=echo))

        if not spec.has_verification:
            return BuildResult(ok=True, attempts=1, report=None, transcript=transcript)

        # Verify deterministically, then repair up to max_repairs times.
        for attempt in range(config.max_repairs + 1):
            report = run_suite(spec.checks, stop_on_failure=config.stop_on_failure)
            if echo:
                print(_banner(f"verification (after attempt {attempt + 1})"), flush=True)
                print(report.to_feedback() or "(no checks)", flush=True)

            if report.ok:
                return BuildResult(ok=True, attempts=attempt + 1, report=report, transcript=transcript)

            if attempt == config.max_repairs:
                return BuildResult(ok=False, attempts=attempt + 1, report=report, transcript=transcript)

            transcript.append(
                await engine.send(repair_prompt(report, attempt + 1, config.max_repairs), echo=echo)
            )

    return BuildResult(ok=False, attempts=config.max_repairs + 1, report=None, transcript=transcript)
