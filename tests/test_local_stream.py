"""Tests for the local engine's streaming turn (GLM/MiniMax-style local models):
text deltas stream live, tool calls accumulate, usage sums, buffered fallback works."""

from __future__ import annotations

import asyncio
import types

from harness.config import EngineConfig, HarnessConfig
from harness.engines.local_engine import LocalEngine
from harness.spec import Spec


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _chunk(content=None, tool=None, usage=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)], usage=usage)


def _tool(index, name, args, cid="c1"):
    return types.SimpleNamespace(index=index, id=cid,
                                 function=types.SimpleNamespace(name=name, arguments=args))


def _usage(n):
    return types.SimpleNamespace(total_tokens=n)


class _FakeCompletions:
    def __init__(self, turns):
        self.turns = turns
        self.i = 0

    async def create(self, **kwargs):
        chunks = self.turns[self.i]
        self.i += 1
        return _FakeStream(chunks)


class _FakeClient:
    def __init__(self, turns):
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(turns))


def _engine(tmp_path, turns, tools_called):
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="glm-4.6"))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = _FakeClient(turns)
    eng._toolbox = types.SimpleNamespace(
        schemas=lambda: [],
        dispatch=lambda name, args: tools_called.append((name, args)) or "TOOL_OK")
    return eng


def test_stream_turn_emits_text_and_dispatches_tools(tmp_path, capsys):
    tools_called = []
    turns = [
        # turn 1: stream text, then a tool call (split across deltas), then usage
        [_chunk(content="work"), _chunk(content="ing"),
         _chunk(tool=[_tool(0, "noop", '{"x":')]),
         _chunk(tool=[_tool(0, "", '1}')]),
         _chunk(usage=_usage(5))],
        # turn 2: just final text, no tools -> loop ends
        [_chunk(content="done"), _chunk(usage=_usage(3))],
    ]
    eng = _engine(tmp_path, turns, tools_called)
    out = asyncio.run(eng.send("build it", echo=True))
    assert out == "working\ndone"
    assert eng.total_tokens == 8
    assert tools_called == [("noop", {"x": 1})]   # split args reassembled + parsed
    printed = capsys.readouterr().out
    assert "working" in printed and "✎ noop" in printed   # streamed live


def test_text_action_fallback_for_non_tool_calling_models(tmp_path, capsys):
    """A model that writes an action as text (no native tool call) still drives a
    tool dispatch via the repair fallback."""
    tools_called = []
    block = ('I will write the file.\n'
             '```action\n{"tool": "write_file", "args": {"path": "a.py", "content": "x=1"}}\n```')
    turns = [
        [_chunk(content=block)],          # turn 1: action as text, no native tool_calls
        [_chunk(content="all done")],     # turn 2: plain text -> finish
    ]
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="tiny-quant"))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = _FakeClient(turns)
    eng._toolbox = types.SimpleNamespace(
        schemas=lambda: [{"type": "function", "function": {"name": "write_file"}}],
        dispatch=lambda name, args: tools_called.append((name, args)) or "WROTE a.py")
    out = asyncio.run(eng.send("build it", echo=True))
    assert tools_called == [("write_file", {"path": "a.py", "content": "x=1"})]
    assert "all done" in out
    assert "tool·text" in capsys.readouterr().out
    # the system prompt got the text-protocol hint
    assert "```action" in eng._messages[0]["content"]


def test_malformed_native_args_are_repaired(tmp_path):
    tools_called = []
    # native tool call with trailing-comma (invalid) JSON args
    bad = _tool(0, "write_file", '{"path": "a.py",}')
    turns = [
        [_chunk(tool=[bad])],
        [_chunk(content="done")],
    ]
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="q"))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = _FakeClient(turns)
    eng._toolbox = types.SimpleNamespace(
        schemas=lambda: [{"type": "function", "function": {"name": "write_file"}}],
        dispatch=lambda n, a: tools_called.append((n, a)) or "ok")
    asyncio.run(eng.send("x", echo=False))
    assert tools_called == [("write_file", {"path": "a.py"})]  # repaired despite trailing comma


def test_stream_pulses_token_meter(tmp_path):
    """The live token/sec meter fires the config callback and records a decode rate."""
    import dataclasses
    pulses = []
    turns = [
        # plain-text turn (no tool calls) -> the loop finishes after one turn
        [_chunk(content="hello "), _chunk(content="world"), _chunk(usage=_usage(4))],
    ]
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="glm-4.6"))
    cfg = dataclasses.replace(cfg, meter_cb=lambda tok, rate, el: pulses.append((tok, rate, el)))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = _FakeClient(turns)
    eng._toolbox = types.SimpleNamespace(schemas=lambda: [], dispatch=lambda n, a: "x")
    out = asyncio.run(eng.send("hi", echo=False))
    assert out == "hello world"
    assert pulses and pulses[-1][0] > 0          # tokens estimated from streamed text
    assert eng.tok_per_sec >= 0.0                # decode rate recorded on the engine


def test_repo_map_prepended_to_first_turn_only(tmp_path):
    (tmp_path / "app.py").write_text("def existing():\n    return 1\n")
    turns = [
        [_chunk(content="ok one")],   # turn 1 (gets the map)
    ]
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="glm-4.6"))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = _FakeClient(turns)
    eng._toolbox = types.SimpleNamespace(schemas=lambda: [], dispatch=lambda n, a: "x")
    asyncio.run(eng.send("add a feature", echo=False))
    first_user = eng._messages[1]["content"]
    assert "Repo map" in first_user and "app.py" in first_user and "def existing" in first_user
    # second call must NOT re-inject the map
    eng._client = _FakeClient([[_chunk(content="ok two")]])
    asyncio.run(eng.send("another change", echo=False))
    assert "Repo map" not in eng._messages[-2]["content"]


def test_stream_dropped_mid_turn_retries_buffered(tmp_path, capsys):
    """A connection drop during streaming must not kill the turn — the engine
    retries the same turn buffered (history is untouched at that point)."""
    class _ExplodingStream:
        def __aiter__(self):
            self._n = 0
            return self

        async def __anext__(self):
            self._n += 1
            if self._n == 1:
                return _chunk(content="partial ")
            raise ConnectionError("server dropped the stream")

    buffered_msg = types.SimpleNamespace(content="full answer", tool_calls=None)
    buffered = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=buffered_msg)], usage=_usage(9))

    class _Completions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(bool(kwargs.get("stream")))
            if kwargs.get("stream"):
                return _ExplodingStream()
            return buffered

    comp = _Completions()
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="glm-4.6"))
    eng = LocalEngine(spec, cfg, "sys")
    eng._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=comp))
    eng._toolbox = types.SimpleNamespace(schemas=lambda: [], dispatch=lambda n, a: "x")
    out = asyncio.run(eng.send("hi", echo=True))
    assert out == "full answer"                 # the buffered retry's result wins
    assert comp.calls == [True, False]          # stream attempt, then buffered retry
    assert eng.total_tokens == 9
    assert "stream dropped" in capsys.readouterr().out


def test_connect_quiet_suppresses_banners(tmp_path, capsys, monkeypatch):
    """Reviewer-style engines with connect_quiet=True don't print warm-up or
    context-window notes on connect."""
    import dataclasses
    import harness.engines.local_engine as le

    class _FakeAsyncOpenAI:
        def __init__(self, **kw):
            pass

    monkeypatch.setitem(__import__("sys").modules, "openai",
                        types.SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI))
    from harness import modelinfo, warmup
    monkeypatch.setattr(warmup, "warm",
                        lambda *a, **k: {"ok": True, "note": "model warm (5ms)"})
    monkeypatch.setattr(modelinfo, "list_models",
                        lambda base: {"models": [{"id": "glm-4.6", "context_length": 32768}]})

    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="glm-4.6",
                                            base_url="http://localhost:11434/v1"))
    quiet = dataclasses.replace(cfg, connect_quiet=True)
    asyncio.run(le.LocalEngine(spec, quiet, "sys").__aenter__())
    assert "model warm" not in capsys.readouterr().out
    asyncio.run(le.LocalEngine(spec, cfg, "sys").__aenter__())
    out = capsys.readouterr().out
    assert "model warm" in out and "context window" in out


def test_buffered_fallback_when_stream_disabled(tmp_path):
    import dataclasses
    tools_called = []
    spec = Spec(name="app", description="d", kind="frontend")
    cfg = HarnessConfig(workspace=str(tmp_path),
                        engine=EngineConfig(provider="local", model="minimax-m3"))
    cfg = dataclasses.replace(cfg, local_stream=False)

    msg = types.SimpleNamespace(content="hi", tool_calls=None)
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=_usage(7))

    class _Buf:
        async def create(self, **kwargs):
            assert "stream" not in kwargs   # buffered path
            return resp

    eng = LocalEngine(spec, cfg, "sys")
    eng._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Buf()))
    eng._toolbox = types.SimpleNamespace(schemas=lambda: [], dispatch=lambda n, a: "x")
    out = asyncio.run(eng.send("hi", echo=False))
    assert out == "hi" and eng.total_tokens == 7
