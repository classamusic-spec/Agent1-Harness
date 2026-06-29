"""Tests for the Claude CLI engine's streaming mode: tool-event rendering and the
stream-json parse (events → live lines, final result + token totals)."""

from __future__ import annotations

import asyncio

from harness.config import EngineConfig, HarnessConfig
from harness.engines.claude_cli_engine import ClaudeCLIEngine, _render_tool
from harness.spec import Spec


def test_render_tool_lines():
    assert _render_tool({"name": "Write", "input": {"file_path": "src/app.js"}}).strip() == "✎ Write app.js"
    assert _render_tool({"name": "Edit", "input": {"file_path": "styles.css"}}).strip() == "✎ Edit styles.css"
    assert _render_tool({"name": "Bash", "input": {"command": "npm test\nignored"}}).strip().startswith("$ npm test")
    assert _render_tool({"name": "Read", "input": {"file_path": "a/b.py"}}).strip() == "· Read b.py"
    assert _render_tool({"name": "Glob", "input": {"pattern": "**/*.ts"}}).strip() == "· Glob **/*.ts"


class _FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""


class _FakeStderr:
    async def read(self, n=-1):
        return b""


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _engine():
    return ClaudeCLIEngine(
        Spec(name="a", description="d", kind="frontend"),
        HarnessConfig(workspace="/tmp", engine=EngineConfig(provider="claude-cli")),
        "sys")


def test_stream_parses_events_tokens_and_echoes(capsys, monkeypatch):
    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"Building the app"}]}}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"index.html"}}]}}\n',
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"python -m pytest"}}]}}\n',
        b'{"type":"result","subtype":"success","result":"done",'
        b'"usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":3},"total_cost_usd":0.02}\n',
    ]

    async def fake_spawn(self, prompt):
        self._proc = _FakeProc(lines)
        return self._proc

    monkeypatch.setattr(ClaudeCLIEngine, "_spawn", fake_spawn)
    eng = _engine()
    out = asyncio.run(eng.send("build it", echo=True))
    assert out == "done"
    assert eng.total_tokens == 18           # 10 + 5 + 3
    assert eng.last_cost_usd == 0.02
    cap = capsys.readouterr().out
    assert "Building the app" in cap        # streamed assistant text
    assert "Write index.html" in cap        # streamed file edit
    assert "$ python -m pytest" in cap      # streamed command


def test_stream_raises_on_error_event(monkeypatch):
    lines = [
        b'{"type":"result","subtype":"error","result":"boom","is_error":true,"usage":{}}\n',
    ]

    async def fake_spawn(self, prompt):
        self._proc = _FakeProc(lines)
        return self._proc

    monkeypatch.setattr(ClaudeCLIEngine, "_spawn", fake_spawn)
    try:
        asyncio.run(_engine().send("x", echo=False))
        assert False, "expected an error"
    except RuntimeError as e:
        assert "error" in str(e).lower()


def test_stream_falls_back_to_assistant_text_without_result(capsys, monkeypatch):
    lines = [
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"partial answer"}]}}\n',
    ]

    async def fake_spawn(self, prompt):
        self._proc = _FakeProc(lines)
        return self._proc

    monkeypatch.setattr(ClaudeCLIEngine, "_spawn", fake_spawn)
    out = asyncio.run(_engine().send("x", echo=False))
    assert out == "partial answer"  # no result event → assistant text is the return
