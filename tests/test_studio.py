"""Tests for the Studio surface: freeform spec synthesis, on-demand tests,
the project marker, and the Claude Code CLI engine's argv builder."""

from __future__ import annotations

import json
import os

from harness.config import EngineConfig, HarnessConfig
from harness.engines.claude_cli_engine import ClaudeCLIEngine, _sum_tokens
from harness.server import (
    _default_checks,
    run_tests,
    studio_meta,
    synth_spec,
    write_studio_meta,
)
from harness.spec import Spec


def test_default_checks_only_for_web_kinds():
    assert _default_checks("frontend")  # has a gate (structure + smoke)
    assert _default_checks("react")
    assert _default_checks("api")  # an API should respond -> smoke check
    assert _default_checks("cli") == []  # non-web, no files -> nothing


def test_synth_spec_builds_valid_spec_with_default_gate():
    spec = synth_spec("my-app", "frontend", "A pomodoro timer")
    assert isinstance(spec, Spec)
    assert spec.kind == "frontend"
    assert spec.description == "A pomodoro timer"
    assert spec.checks  # default web gate applied


def test_synth_spec_respects_explicit_checks():
    spec = synth_spec("x", "cli", "thing", verification=[{"name": "t", "command": "true"}])
    assert [c.name for c in spec.checks] == ["t"]


def test_studio_marker_roundtrip(tmp_path):
    ws = str(tmp_path / "proj")
    write_studio_meta(ws, {"kind": "frontend", "engine": "claude-cli", "checks": []})
    meta = studio_meta(ws)
    assert meta["kind"] == "frontend"
    assert meta["engine"] == "claude-cli"
    assert os.path.isfile(os.path.join(ws, ".studio.json"))


def test_run_tests_passes_for_present_index(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "index.html").write_text(
        "<!doctype html><html><head><title>App</title></head>"
        "<body><h1>Hello world — this file is comfortably over eighty bytes</h1></body></html>")
    result = run_tests(str(ws), _default_checks("frontend"))
    assert result["ok"] is True
    assert result["results"][0]["name"] == "app builds"


def test_run_tests_fails_when_app_missing(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    result = run_tests(str(ws), _default_checks("frontend"))
    assert result["ok"] is False


def test_run_tests_no_checks_is_ok(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    result = run_tests(str(ws), [])
    assert result["ok"] is True
    assert result["results"] == []


def test_sum_tokens_adds_prompt_completion_and_cache():
    usage = {
        "input_tokens": 10, "output_tokens": 20,
        "cache_creation_input_tokens": 5, "cache_read_input_tokens": 7,
    }
    assert _sum_tokens(usage) == 42
    assert _sum_tokens({}) == 0
    assert _sum_tokens(None) == 0


def test_claude_cli_argv_includes_model_system_and_tools():
    spec = synth_spec("app", "frontend", "desc")
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="claude-cli", model="sonnet"))
    eng = ClaudeCLIEngine(spec, cfg, "PERSONA-PROMPT")
    argv = eng._argv("do the thing")
    assert argv[0].endswith("claude") or argv[0] == "claude"
    assert "-p" in argv and "do the thing" in argv
    assert "--permission-mode" in argv and "acceptEdits" in argv
    assert "--model" in argv and "sonnet" in argv
    assert "--append-system-prompt" in argv and "PERSONA-PROMPT" in argv
    assert "--output-format" in argv and "json" in argv
    # allowed tools are pre-permitted so headless runs don't prompt
    assert "Write" in argv and "Edit" in argv and "Read" in argv


def test_claude_cli_terminate_is_safe_without_proc():
    spec = synth_spec("app", "frontend", "desc")
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="claude-cli"))
    eng = ClaudeCLIEngine(spec, cfg, "")
    # No subprocess running yet — terminate must be a harmless no-op.
    assert eng._proc is None
    eng.terminate()


def test_job_has_cancel_fields():
    from harness.server import Job
    job = Job("1", "/tmp/ws")
    assert job.cancelled is False
    assert job.engine is None


def test_claude_cli_argv_omits_model_when_unset():
    spec = synth_spec("app", "frontend", "desc")
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="claude-cli", model=""))
    eng = ClaudeCLIEngine(spec, cfg, "")
    argv = eng._argv("x")
    assert "--model" not in argv
    assert "--append-system-prompt" not in argv  # empty system prompt omitted
