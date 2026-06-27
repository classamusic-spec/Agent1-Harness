"""Prompt construction for the build agent."""

from __future__ import annotations

from harness.spec import Spec
from harness.verifier import VerificationReport

SYSTEM_PROMPT = """\
You are an autonomous software engineer building a new application from a spec.

Rules of engagement:
- Work ONLY inside the current working directory. Do not touch anything outside it.
- Build the simplest thing that fully satisfies the spec. Do not over-engineer,
  add speculative features, or scaffold beyond what is asked.
- Your work is judged by an automated verification suite (build / typecheck /
  lint / tests). You are not done until it passes. Saying "done" is not enough —
  the checks must be green.
- Before ending your turn, run the `verify` tool and fix anything it reports.
- Prefer the standard library and minimal dependencies. If you add a dependency,
  make sure the verification commands can still run.
- Make small, coherent files that read like real production code.
"""


def _constraints_block(spec: Spec) -> str:
    if not spec.constraints:
        return ""
    bullets = "\n".join(f"- {c}" for c in spec.constraints)
    return f"\nConstraints:\n{bullets}\n"


def _checks_block(spec: Spec) -> str:
    if not spec.checks:
        return "\n(No verification commands were provided.)\n"
    bullets = "\n".join(f"- {c.name}: `{c.command}`" for c in spec.checks)
    return f"\nThe verification suite that must pass:\n{bullets}\n"


def build_prompt(spec: Spec) -> str:
    return (
        f"Build the following application: **{spec.name}**\n"
        f"Target language/stack: {spec.language}\n\n"
        f"Specification:\n{spec.description}\n"
        f"{_constraints_block(spec)}"
        f"{_checks_block(spec)}\n"
        "Implement the application now in the current directory, then run the "
        "`verify` tool and fix any failures before finishing."
    )


def repair_prompt(report: VerificationReport, attempt: int, max_attempts: int) -> str:
    return (
        f"Verification failed (repair attempt {attempt}/{max_attempts}). "
        "Here is the authoritative report from the harness:\n\n"
        f"{report.to_feedback()}\n\n"
        "Diagnose the root cause and fix it. Change only what is needed to make "
        "the failing checks pass. Then run the `verify` tool to confirm."
    )
