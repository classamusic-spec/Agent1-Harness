"""Tests for project scaffolds: registry, materialization, and that the two
zero-dependency scaffolds actually boot + pass their server-backed checks."""

from __future__ import annotations

import os

from harness import fullstack, scaffolds
from harness.verifier import Check


def test_registry_lists_expected_scaffolds():
    names = {s["name"] for s in scaffolds.list_scaffolds()}
    assert {"static", "python-api", "vite-react", "fastapi"} <= names
    for s in scaffolds.list_scaffolds():
        assert s["run"] and s["label"] and s["kind"]


def test_apply_writes_files_without_overwriting(tmp_path):
    sc = scaffolds.apply("static", str(tmp_path))
    assert sc and sc.name == "static"
    for rel in ("index.html", "styles.css", "app.js"):
        assert (tmp_path / rel).is_file()
    # don't clobber edits
    (tmp_path / "app.js").write_text("// edited")
    scaffolds.apply("static", str(tmp_path))
    assert (tmp_path / "app.js").read_text() == "// edited"


def test_apply_nested_paths(tmp_path):
    scaffolds.apply("python-api", str(tmp_path))
    assert (tmp_path / "server.py").is_file()
    assert (tmp_path / "public" / "index.html").is_file()
    assert (tmp_path / "public" / "app.js").is_file()


def test_apply_unknown_returns_none(tmp_path):
    assert scaffolds.apply("nope", str(tmp_path)) is None


def _run_scaffold_checks(name: str, tmp_path) -> bool:
    sc = scaffolds.apply(name, str(tmp_path))
    checks = [Check(name=c["name"], command=c["command"], cwd=str(tmp_path),
                    needs_server=bool(c.get("needs_server")),
                    allow_failure=bool(c.get("allow_failure"))) for c in sc.checks]
    rep = fullstack.verify_checks(checks, str(tmp_path), run_command=sc.run, stop_on_failure=True)
    return rep.ok, {r.name: (r.ok, r.skipped) for r in rep.results}


def test_static_scaffold_boots_and_passes(tmp_path):
    ok, results = _run_scaffold_checks("static", tmp_path)
    assert ok, results
    assert results["app responds"][0] is True  # server booted, smoke passed


def test_python_api_scaffold_boots_and_serves_api(tmp_path):
    ok, results = _run_scaffold_checks("python-api", tmp_path)
    assert ok, results
    assert results["api health"][0] is True   # /api/health responded 200
    assert results["app responds"][0] is True  # static frontend served
