"""Server-backed verification: run static checks, then boot the dev server and
run the checks that need a live app (e2e / smoke / API contract), then stop it.

Static checks (install/build/typecheck/lint/tests) run as usual. Any check with
`needs_server=True` runs against the running dev server, with `$APP_URL`
substituted for its address. If the static gate fails, server checks are skipped
(no point testing a broken build).
"""

from __future__ import annotations

import dataclasses

from harness import env as envmod
from harness.verifier import Check, CheckResult, VerificationReport, run_suite


def _skipped(check: Check, why: str) -> CheckResult:
    return CheckResult(name=check.name, command=check.command, returncode=0,
                       stdout="", stderr="", ok=False, skipped=True, error=why)


def verify_checks(checks: list[Check], workspace: str, *, run_command: str | None = None,
                  runner=None, stop_on_failure: bool = True) -> VerificationReport:
    """Run `checks`, starting the dev server for any that need it.

    The workspace `.env` (DB path, secrets) is loaded once and injected into every
    check's environment and into the dev server, so migrations and the running app
    share the same configuration. `.env` is materialised from `.env.example` first
    if needed (generating a fresh secret)."""
    check_env = envmod.ensure_env(workspace)
    # Default every check's cwd to the workspace (callers may leave it unset).
    checks = [c if c.cwd else dataclasses.replace(c, cwd=workspace) for c in checks]
    static = [c for c in checks if not c.needs_server]
    server_checks = [c for c in checks if c.needs_server]
    report = run_suite(static, stop_on_failure=stop_on_failure, runner=runner, env=check_env)
    if not server_checks:
        return report

    if stop_on_failure and not report.ok:
        for c in server_checks:
            report.results.append(_skipped(c, "skipped: static checks failed"))
        return report

    from harness import runtime
    cmd = run_command or (runtime.detect_command(workspace) or {}).get("command")
    if not cmd:
        for c in server_checks:
            report.results.append(_skipped(c, "skipped: no run command (none detected)"))
        return report

    from harness import runtimeerrors
    with runtime.serve(workspace, cmd, env=check_env) as svc:
        base = f"http://127.0.0.1:{svc.port}"
        subbed = [dataclasses.replace(c, command=c.command.replace("$APP_URL", base))
                  for c in server_checks]
        rep = run_suite(subbed, stop_on_failure=False, runner=runner, env=check_env)
        log_lines = svc.logs()[0]
        # Lead failing checks with the *extracted* runtime error (traceback/stack),
        # not just the raw tail, so repair prompts target the real cause.
        if not rep.ok:
            block = runtimeerrors.from_service_logs(log_lines)
            for r in rep.results:
                if not r.ok and not r.skipped:
                    r.error = (r.error or "") + "\n--- dev server runtime error ---\n" + block
        # A boot-time crash (or a 500 from an exception) can leave checks "passing"
        # while the server log carries a traceback — surface it as a real failure.
        elif runtimeerrors.has_error(log_lines):
            block = runtimeerrors.extract("\n".join(log_lines))
            rep.results.append(CheckResult(
                name="runtime", command="(dev server runtime check)", returncode=1,
                stdout="", stderr=block, ok=False,
                error="the app logged a runtime error while serving:\n" + block))
    report.results.extend(rep.results)
    return report
