"""Offline tests for the verification gate. No Claude / network required."""

from __future__ import annotations

from harness.verifier import Check, run_check, run_suite


def test_passing_check():
    result = run_check(Check(name="echo", command="echo hello"))
    assert result.ok
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_failing_check():
    result = run_check(Check(name="fail", command="exit 3"))
    assert not result.ok
    assert result.returncode == 3


def test_allow_failure_marks_ok():
    result = run_check(Check(name="advisory", command="exit 1", allow_failure=True))
    assert result.ok
    assert result.returncode == 1


def test_timeout_is_data_not_exception():
    result = run_check(Check(name="slow", command="sleep 5", timeout=1))
    assert not result.ok
    assert result.error and "timed out" in result.error


def test_suite_stops_on_first_failure():
    checks = [
        Check(name="a", command="exit 1"),
        Check(name="b", command="echo should-not-run"),
    ]
    report = run_suite(checks, stop_on_failure=True)
    assert not report.ok
    assert report.results[0].ok is False
    assert report.results[1].skipped is True


def test_suite_continues_when_not_stopping():
    checks = [
        Check(name="a", command="exit 1"),
        Check(name="b", command="echo ran"),
    ]
    report = run_suite(checks, stop_on_failure=False)
    assert not report.ok  # a failed
    assert report.results[1].ok is True  # b still ran
    assert report.results[1].skipped is False


def test_all_pass():
    checks = [Check(name="a", command="true"), Check(name="b", command="echo ok")]
    report = run_suite(checks)
    assert report.ok
    assert report.failures == []


def test_feedback_includes_failure_detail():
    report = run_suite([Check(name="boom", command="echo oops 1>&2; exit 2")])
    fb = report.to_feedback()
    assert "FAIL" in fb
    assert "boom" in fb
