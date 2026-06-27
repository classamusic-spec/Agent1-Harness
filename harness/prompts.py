"""Prompt construction for the build agent."""

from __future__ import annotations

from harness.spec import Spec
from harness.verifier import VerificationReport


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


def with_lessons(prompt: str, lessons: str) -> str:
    """Prepend prior-build lessons to a prompt, if any."""
    return f"{lessons}\n\n{prompt}" if lessons else prompt


def review_repair_prompt(verdict, attempt: int, max_attempts: int) -> str:
    lines = []
    for f in verdict.findings:
        loc = f" [{f.location}]" if f.location else ""
        lines.append(f"- ({f.severity}) {f.title}{loc}: {f.detail}")
    findings = "\n".join(lines) or "(no structured findings provided)"
    return (
        f"An independent reviewer rejected the build (review fix {attempt}/{max_attempts}). "
        f"Summary: {verdict.summary}\n\nAddress these findings, fixing the blocker/major "
        f"items first:\n{findings}\n\n"
        "Make the changes, then ensure the verification suite still passes."
    )


def repair_prompt(report: VerificationReport, attempt: int, max_attempts: int) -> str:
    return (
        f"Verification failed (repair attempt {attempt}/{max_attempts}). "
        "Here is the authoritative report from the harness:\n\n"
        f"{report.to_feedback()}\n\n"
        "Diagnose the root cause and fix it. Change only what is needed to make "
        "the failing checks pass. Then run the `verify` tool to confirm."
    )
