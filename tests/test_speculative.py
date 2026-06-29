"""Tests for speculative/parallel verify: pick the right fast checks per stack,
run them, and report advisory results — without waiting for the end-of-turn gate."""

from __future__ import annotations

from harness import speculative
from harness.verifier import CheckResult, VerificationReport


def test_fast_checks_python(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    checks = speculative.fast_checks(str(tmp_path))
    assert [c.name for c in checks] == ["compiles"]
    assert checks[0].allow_failure is True
    assert "compileall" in checks[0].command


def test_fast_checks_node_needs_tsconfig_and_modules(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    # no tsconfig / node_modules yet -> nothing fast to run safely
    assert speculative.fast_checks(str(tmp_path)) == []
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "node_modules").mkdir()
    checks = speculative.fast_checks(str(tmp_path))
    assert [c.name for c in checks] == ["typecheck"]
    assert "--no-install" in checks[0].command


def test_fast_checks_empty_for_unknown_stack(tmp_path):
    (tmp_path / "README.md").write_text("hi")
    assert speculative.fast_checks(str(tmp_path)) == []


def _ok_report(*names):
    return VerificationReport(results=[
        CheckResult(name=n, command="c", returncode=0, stdout="", stderr="", ok=True)
        for n in names])


def test_run_once_reports_results(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    seen = []
    captured = {}

    def fake_run_suite(checks, stop_on_failure=True, runner=None, env=None):
        captured["checks"] = checks
        captured["stop"] = stop_on_failure
        return _ok_report(*[c.name for c in checks])

    import harness.verifier as verifier
    orig = verifier.run_suite
    verifier.run_suite = fake_run_suite
    try:
        spec = speculative.Speculator(str(tmp_path), on_result=seen.append)
        results = spec.run_once()
    finally:
        verifier.run_suite = orig

    assert results == [{"name": "compiles", "ok": True, "error": ""}]
    assert seen == [results]              # on_result was called with the results
    assert captured["stop"] is False      # never halts the suite — runs every check


def test_run_once_noop_when_no_checks(tmp_path):
    (tmp_path / "README.md").write_text("hi")
    seen = []
    spec = speculative.Speculator(str(tmp_path), on_result=seen.append)
    assert spec.run_once() == []
    assert seen == []


def test_report_line_pass_and_fail():
    assert "PASS" in speculative.report_line([{"name": "compiles", "ok": True}])
    line = speculative.report_line(
        [{"name": "compiles", "ok": False}, {"name": "typecheck", "ok": True}])
    assert "FAIL" in line and "compiles" in line and "typecheck" not in line.split("—")[1]


def test_watch_is_noop_without_fast_checks(tmp_path, capsys):
    (tmp_path / "README.md").write_text("hi")
    with speculative.watch(str(tmp_path)) as spec:
        assert spec is None            # nothing to watch -> no thread started
    assert capsys.readouterr().out == ""


def test_watch_disabled(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    with speculative.watch(str(tmp_path), enabled=False) as spec:
        assert spec is None


def test_run_once_with_explicit_runner_reports_failure(tmp_path):
    """End-to-end with a fake runner that fails the compile -> advisory FAIL line."""
    (tmp_path / "main.py").write_text("def broken(:\n")  # syntax error

    class _Proc:
        def __init__(self, rc, err):
            self.returncode, self.stdout, self.stderr = rc, "", err

    class _Runner:
        def run(self, command, cwd, timeout, env=None):
            return _Proc(1, "SyntaxError: invalid syntax")

    seen = []
    spec = speculative.Speculator(str(tmp_path), on_result=seen.append, runner=_Runner())
    results = spec.run_once()
    # compileall is allow_failure -> the CheckResult.ok stays True, but the raw
    # error text is still surfaced for the advisory line.
    assert seen and seen[0][0]["name"] == "compiles"
    assert "SyntaxError" in results[0]["error"]
