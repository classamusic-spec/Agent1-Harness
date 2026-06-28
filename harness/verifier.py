"""Deterministic verification gate.

This module is intentionally free of any Claude / LLM dependency so it can be
unit-tested offline and trusted as the source of truth. The agent never decides
whether it succeeded — this code does, by running real commands and inspecting
exit codes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

# How much of each command's output to keep when feeding failures back to the
# model. Full logs can be huge; the tail almost always carries the error.
_MAX_OUTPUT_CHARS = 4000


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} chars truncated]...\n{text[-tail:]}"


@dataclass
class Check:
    """A single verification step, e.g. build, typecheck, lint, or tests."""

    name: str
    command: str
    cwd: str | None = None
    timeout: int = 600
    # When True, a non-zero exit does not fail the suite (useful for advisory
    # checks like an optional linter).
    allow_failure: bool = False


@dataclass
class CheckResult:
    name: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    ok: bool
    skipped: bool = False
    error: str | None = None  # set when the command could not be run at all

    def summary(self) -> str:
        status = "PASS" if self.ok else ("SKIP" if self.skipped else "FAIL")
        lines = [f"[{status}] {self.name}: {self.command} (exit {self.returncode})"]
        if self.error:
            lines.append(f"  error: {self.error}")
        out = _truncate((self.stdout or "").strip())
        err = _truncate((self.stderr or "").strip())
        if out:
            lines.append("  stdout:\n" + _indent(out))
        if err:
            lines.append("  stderr:\n" + _indent(err))
        return "\n".join(lines)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


@dataclass
class VerificationReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """The suite passes only if every non-advisory check passed."""
        return all(r.ok for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok and not r.skipped]

    def to_feedback(self) -> str:
        """Render the report as feedback to hand back to the agent on a repair turn."""
        return "\n\n".join(r.summary() for r in self.results)


def failure_delta(prev: "VerificationReport | None", cur: VerificationReport) -> str:
    """Describe how the set of failing checks changed between two rounds."""
    cur_fail = {r.name for r in cur.failures}
    if prev is None:
        return ""
    prev_fail = {r.name for r in prev.failures}
    fixed = sorted(prev_fail - cur_fail)
    new = sorted(cur_fail - prev_fail)
    still = sorted(cur_fail & prev_fail)
    parts = []
    if fixed:
        parts.append("now passing: " + ", ".join(fixed))
    if new:
        parts.append("newly broken: " + ", ".join(new))
    if still:
        parts.append("still failing: " + ", ".join(still))
    return "; ".join(parts)


def run_check(check: Check, runner=None) -> CheckResult:
    """Run a single check via a CommandRunner. Never raises — failures are data."""
    if runner is None:
        from harness.sandbox import HostRunner
        runner = HostRunner()
    try:
        proc = runner.run(check.command, check.cwd, check.timeout)
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            name=check.name,
            command=check.command,
            returncode=124,
            stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            ok=check.allow_failure,
            error=f"timed out after {check.timeout}s",
        )
    except (OSError, ValueError) as exc:  # command not found, bad invocation, etc.
        return CheckResult(
            name=check.name,
            command=check.command,
            returncode=127,
            stdout="",
            stderr="",
            ok=check.allow_failure,
            error=str(exc),
        )

    passed = proc.returncode == 0 or check.allow_failure
    return CheckResult(
        name=check.name,
        command=check.command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        ok=passed,
    )


def run_suite(checks: list[Check], stop_on_failure: bool = True, runner=None) -> VerificationReport:
    """Run all checks in order.

    With `stop_on_failure` (the default), the first hard failure stops the run
    and the remaining checks are recorded as skipped — there is no point running
    tests if the build is broken.
    """
    report = VerificationReport()
    halted = False
    for check in checks:
        if halted:
            report.results.append(
                CheckResult(
                    name=check.name,
                    command=check.command,
                    returncode=0,
                    stdout="",
                    stderr="",
                    ok=False,
                    skipped=True,
                    error="skipped after an earlier failure",
                )
            )
            continue

        result = run_check(check, runner=runner)
        report.results.append(result)
        if not result.ok and not check.allow_failure and stop_on_failure:
            halted = True

    return report
