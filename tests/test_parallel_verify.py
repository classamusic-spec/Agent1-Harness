"""Tests for parallel verification: adjacent parallel checks run concurrently,
order and stop-on-failure semantics are preserved."""

from __future__ import annotations

import threading

from harness.verifier import Check, _batches, run_suite


class _Proc:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def test_batches_grouping():
    c = lambda n, p: Check(name=n, command="true", parallel=p)  # noqa: E731
    groups = _batches([c("install", False), c("typecheck", True), c("lint", True),
                       c("e2e", False), c("a", True)])
    assert [[x.name for x in g] for g in groups] == \
        [["install"], ["typecheck", "lint"], ["e2e"], ["a"]]


def test_parallel_checks_actually_run_concurrently():
    """Both checks must be in flight at once: each blocks on a 2-party barrier,
    so a serial runner would deadlock (barrier timeout -> failure)."""
    barrier = threading.Barrier(2, timeout=5)

    class _Runner:
        def run(self, command, cwd, timeout, env=None):
            barrier.wait()          # only passes if the other check is also running
            return _Proc(0)

    checks = [Check(name="a", command="x", parallel=True),
              Check(name="b", command="y", parallel=True)]
    report = run_suite(checks, runner=_Runner())
    assert report.ok
    assert [r.name for r in report.results] == ["a", "b"]   # order preserved


def test_serial_checks_do_not_overlap():
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    class _Runner:
        def run(self, command, cwd, timeout, env=None):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            import time
            time.sleep(0.02)
            with lock:
                active["n"] -= 1
            return _Proc(0)

    checks = [Check(name=str(i), command="x") for i in range(4)]  # default serial
    run_suite(checks, runner=_Runner())
    assert active["max"] == 1


def test_parallel_batch_failure_skips_later_checks():
    class _Runner:
        def run(self, command, cwd, timeout, env=None):
            return _Proc(1 if command == "bad" else 0)

    checks = [Check(name="t1", command="ok", parallel=True),
              Check(name="t2", command="bad", parallel=True),
              Check(name="after", command="ok")]
    report = run_suite(checks, stop_on_failure=True, runner=_Runner())
    by = {r.name: r for r in report.results}
    assert by["t1"].ok and not by["t2"].ok
    assert by["after"].skipped                       # halted after the batch
    assert not report.ok


def test_allow_failure_in_batch_does_not_halt():
    class _Runner:
        def run(self, command, cwd, timeout, env=None):
            return _Proc(1 if command == "lint" else 0)

    checks = [Check(name="lint", command="lint", parallel=True, allow_failure=True),
              Check(name="tests", command="ok", parallel=True),
              Check(name="after", command="ok")]
    report = run_suite(checks, runner=_Runner())
    assert report.ok and not report.results[-1].skipped


def test_spec_roundtrips_parallel_flag():
    from harness.spec import parse_spec, spec_to_dict
    spec = parse_spec({"name": "x", "description": "d",
                       "verification": [{"name": "t", "command": "true", "parallel": True}]})
    assert spec.checks[0].parallel is True
    assert spec_to_dict(spec)["verification"][0]["parallel"] is True


def test_node_defaults_mark_fanout_parallel(tmp_path):
    import json as _json
    from harness import stacks
    (tmp_path / "package.json").write_text(_json.dumps(
        {"scripts": {"build": "vite build", "test": "vitest run"},
         "devDependencies": {"typescript": "^5", "eslint": "^9"}}))
    (tmp_path / "tsconfig.json").write_text("{}")
    checks = {c.name: c for c in stacks.default_checks(str(tmp_path), "frontend")}
    assert not checks["install"].parallel and not checks["build"].parallel
    assert checks["typecheck"].parallel and checks["lint"].parallel and checks["tests"].parallel
