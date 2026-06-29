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


def build_prompt(spec: Spec, design_brief: str = "", scaffold_note: str = "",
                 profile_note: str = "") -> str:
    brief = (f"\nReference design to match (study this closely and reproduce its look "
             f"and feel):\n{design_brief}\n") if design_brief else ""
    scaffold = (f"\nStarting point: {scaffold_note}\nBuild ON this base — keep its structure, "
                f"conventions, and run command; edit and extend the existing files rather than "
                f"replacing them.\n") if scaffold_note else ""
    profile = (f"\n## House style\n{profile_note}\n") if profile_note else ""
    return (
        f"Build the following application: **{spec.name}**\n"
        f"Target language/stack: {spec.language}\n\n"
        f"Specification:\n{spec.description}\n"
        f"{_constraints_block(spec)}"
        f"{scaffold}"
        f"{profile}"
        f"{brief}"
        f"{_checks_block(spec)}\n"
        "Implement the application now in the current directory, then run the "
        "`verify` tool and fix any failures before finishing."
    )


def visual_repair_prompt(differences: list[str]) -> str:
    bullets = "\n".join(f"- {d}" for d in differences) or "- (general visual polish)"
    return (
        "The current build does not yet visually match the reference design. A visual "
        "comparison found these differences — fix them in the existing files without breaking "
        "functionality, staying faithful to the reference's look and feel:\n"
        f"{bullets}\n\nApply the changes now."
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


def security_repair_prompt(findings, attempt: int, max_attempts: int) -> str:
    lines = [f"- ({f.severity}) {f.rule} [{f.file}:{f.line}]: {f.message}\n    {f.snippet}"
             for f in findings]
    body = "\n".join(lines) or "(no findings)"
    return (
        f"A deterministic security scan flagged blocking issues (security fix "
        f"{attempt}/{max_attempts}). Fix each at its real root cause — do not merely "
        f"silence the pattern:\n{body}\n\n"
        "Guidance: move secrets to environment variables (never hardcode), use "
        "parameterised queries (never string-build SQL), avoid shell=True / os.system "
        "(pass argument lists), and never disable TLS verification. Make the changes, "
        "then ensure the verification suite still passes."
    )


def _context_blocks(diff: str, delta: str) -> str:
    out = ""
    if delta:
        out += f"\nWhat changed since the last round: {delta}\n"
    if diff:
        out += f"\nDiff of your last change (review it — a fix may have caused a regression):\n```diff\n{diff}\n```\n"
    return out


def human_feedback_prompt(message: str) -> str:
    return (
        "A human reviewer rejected the build with this feedback:\n\n"
        f"{message}\n\n"
        "Address it specifically and minimally. Then run the `verify` tool to "
        "confirm the verification suite still passes."
    )


def repair_prompt(report: VerificationReport, attempt: int, max_attempts: int,
                  *, diff: str = "", delta: str = "") -> str:
    return (
        f"Verification failed (repair attempt {attempt}/{max_attempts}). "
        "Here is the authoritative report from the harness:\n\n"
        f"{report.to_feedback()}\n"
        f"{_context_blocks(diff, delta)}\n"
        "Diagnose the ROOT CAUSE and fix it. Change only what is needed to make "
        "the failing checks pass. Then run the `verify` tool to confirm."
    )


def escalation_prompt(report: VerificationReport, attempt: int, max_attempts: int,
                      *, diff: str = "", delta: str = "") -> str:
    return (
        "You are a fresh debugging specialist brought in because previous attempts "
        f"got STUCK — the same checks keep failing (escalation {attempt}/{max_attempts}).\n\n"
        "Do not assume the earlier approach was correct. Re-read the relevant files, "
        "form a new hypothesis about the real root cause, and take a DIFFERENT approach "
        "than what's already been tried.\n\n"
        f"Current verification report:\n\n{report.to_feedback()}\n"
        f"{_context_blocks(diff, delta)}\n"
        "Fix the root cause, then run the `verify` tool to confirm."
    )
