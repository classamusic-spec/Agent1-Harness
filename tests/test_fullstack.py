"""Tests for stack-aware default checks and server-backed verification."""

from __future__ import annotations

import json

from harness import fullstack, stacks
from harness.verifier import Check


def test_detect_stack(tmp_path):
    assert stacks.detect_stack(str(tmp_path)) == "unknown"
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert stacks.detect_stack(str(tmp_path)) == "static"
    (tmp_path / "package.json").write_text("{}")
    assert stacks.detect_stack(str(tmp_path)) == "node"


def test_default_checks_node_has_install_build_and_smoke(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"build": "vite build"}, "devDependencies": {"typescript": "^5"}}))
    (tmp_path / "tsconfig.json").write_text("{}")
    names = [c.name for c in stacks.default_checks(str(tmp_path), "frontend")]
    assert "install" in names and "build" in names and "typecheck" in names
    assert "app responds" in names  # the smoke check
    smoke = next(c for c in stacks.default_checks(str(tmp_path), "frontend") if c.name == "app responds")
    assert smoke.needs_server and "$APP_URL" in smoke.command


def test_default_checks_static_kind(tmp_path):
    checks = stacks.default_checks(None, "frontend")
    names = [c.name for c in checks]
    assert "app builds" in names and "app responds" in names


def test_default_checks_cli_kind_has_no_smoke():
    checks = stacks.default_checks(None, "cli")
    assert checks == []  # non-web, no files -> nothing to smoke


def test_verify_checks_boots_server_and_smoke_passes(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>FS</title></head>"
        "<body><h1>full-stack verify works — the server booted and responded</h1></body></html>")
    checks = [
        Check(name="exists", command="test -f index.html", cwd=str(tmp_path)),
        Check(name="app responds", command=stacks.SMOKE_CMD, cwd=str(tmp_path), needs_server=True),
    ]
    rep = fullstack.verify_checks(checks, str(tmp_path),
                                  run_command="python -m http.server $PORT", stop_on_failure=True)
    by = {r.name: r for r in rep.results}
    assert by["exists"].ok and by["app responds"].ok
    assert rep.ok


def test_verify_checks_skips_server_when_static_fails(tmp_path):
    checks = [
        Check(name="exists", command="test -f index.html", cwd=str(tmp_path)),  # fails: no file
        Check(name="app responds", command=stacks.SMOKE_CMD, cwd=str(tmp_path), needs_server=True),
    ]
    rep = fullstack.verify_checks(checks, str(tmp_path),
                                  run_command="python -m http.server $PORT", stop_on_failure=True)
    by = {r.name: r for r in rep.results}
    assert not by["exists"].ok
    assert by["app responds"].skipped  # never started the server


def test_verify_checks_no_server_command(tmp_path):
    (tmp_path / "index.html").write_text("x" * 200)
    checks = [Check(name="smoke", command=stacks.SMOKE_CMD, cwd=str(tmp_path), needs_server=True)]
    # detect_command finds index.html -> static http.server, so this actually runs;
    # force "no command" by using a non-web dir
    empty = tmp_path / "empty"; empty.mkdir()
    checks2 = [Check(name="smoke", command="true", cwd=str(empty), needs_server=True)]
    rep = fullstack.verify_checks(checks2, str(empty), run_command=None, stop_on_failure=False)
    assert rep.results[0].skipped  # no run command detected -> skipped
