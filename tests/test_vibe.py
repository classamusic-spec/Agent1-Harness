"""Tests for the vibe-coding features: preview injection (devtools + element
picker), workspace mtime (hot reload), and the point-&-edit target weaving."""

from __future__ import annotations

import os
import time

from harness.server import (
    _inject_devtools,
    _inject_preview,
    workspace_mtime,
)


def test_inject_devtools_adds_picker_and_console():
    out = _inject_devtools(b"<html><head></head><body></body></html>").decode()
    assert "__harnessDev" in out          # console capture
    assert "__harnessPick" in out         # element picker
    assert "__harnessPicked" in out       # posts the picked descriptor
    # injected right after <head>
    assert out.index("<head>") < out.index("__harnessPick")


def test_inject_preview_adds_base_and_picker():
    out = _inject_preview(b"<html><head></head><body></body></html>", "myapp").decode()
    assert '<base href="/preview/myapp/">' in out
    assert "__harnessPick" in out


def test_inject_handles_missing_head():
    out = _inject_devtools(b"<body>hi</body>").decode()
    assert "__harnessPick" in out  # still injected (at the top) when there's no <head>


def test_workspace_mtime_tracks_changes(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    m1 = workspace_mtime(str(tmp_path))
    assert m1 > 0
    time.sleep(0.02)
    (tmp_path / "b.txt").write_text("two")
    # bump b.txt's mtime explicitly to avoid filesystem granularity flakiness
    future = m1 + 5
    os.utime(tmp_path / "b.txt", (future, future))
    assert workspace_mtime(str(tmp_path)) >= future


def test_workspace_mtime_skips_caches(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    base = workspace_mtime(str(tmp_path))
    cache = tmp_path / "node_modules"
    cache.mkdir()
    f = cache / "huge.js"
    f.write_text("y")
    os.utime(f, (base + 100, base + 100))  # newer, but in a skipped dir
    assert workspace_mtime(str(tmp_path)) < base + 100


def test_workspace_mtime_missing_dir_is_zero(tmp_path):
    assert workspace_mtime(str(tmp_path / "nope")) == 0.0


def test_iterate_weaves_target_into_instruction(monkeypatch, tmp_path):
    """_execute_iterate folds a picked element's selector/markup into the change
    instruction before it reaches the engine."""
    from harness import server
    from harness.config import EngineConfig

    captured = {}

    async def fake_turn(engine, instruction):
        captured["instruction"] = instruction
        return (0, "")

    # Stub the engine turn, verification, snapshot, and engine construction.
    monkeypatch.setattr(server, "_run_engine_turn", fake_turn)
    monkeypatch.setattr(server, "run_tests", lambda ws, checks=None: {"ok": True, "results": []})
    monkeypatch.setattr(server, "_snapshot", lambda *a, **k: None)
    monkeypatch.setattr("harness.engines.base.make_engine", lambda spec, config: object())

    ws = tmp_path / "app"
    ws.mkdir()
    (ws / "index.html").write_text("<button class='cta'>Buy</button>")

    console = server.Console(specs_dir=str(tmp_path), workspaces_dir=str(tmp_path))
    job = server.Job("j1", str(ws))
    console._execute_iterate(job, {
        "instruction": "make it green",
        "engine": "claude-cli",
        "target": {"selector": "button.cta", "label": "button.cta",
                   "text": "Buy", "html": "<button class='cta'>Buy</button>"},
    })
    instr = captured["instruction"]
    assert "make it green" in instr
    assert "button.cta" in instr
    assert "pointed at this element" in instr
    assert "<button class='cta'>Buy</button>" in instr
