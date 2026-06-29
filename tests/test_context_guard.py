"""Tests for the context-window guard + deterministic history compaction."""

from __future__ import annotations

from harness import context_guard as cg


def _msg(role, content="", tool_calls=None):
    m = {"role": role, "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return m


def _tc(name, args):
    return {"id": "x", "type": "function", "function": {"name": name, "arguments": args}}


def test_estimate_tokens_counts_content_and_tool_args():
    msgs = [_msg("system", "x" * 40),
            _msg("assistant", "", [_tc("write_file", '{"path":"a.py","content":"y"}')])]
    # 40 chars ~10 tokens + overhead, plus tool name + args tokens + overhead
    assert cg.estimate_tokens(msgs) > 12


def test_estimate_handles_vision_list_content():
    msg = _msg("user", [{"type": "text", "text": "hello"},
                        {"type": "image_url", "image_url": {"url": "data:..."}}])
    # text + a small "[image]" placeholder are counted; the data URL is not.
    assert cg.estimate_message_tokens(msg) > 0
    assert cg.estimate_message_tokens(msg) < 20


def test_compact_keeps_system_and_recent_folds_middle():
    msgs = [_msg("system", "persona")]
    for i in range(10):
        msgs.append(_msg("assistant", "", [_tc("write_file", f'{{"path":"f{i}.py"}}')]))
        msgs.append(_msg("tool", f"wrote f{i}.py"))
    new, dropped = cg.compact(msgs, keep_recent=4)
    assert dropped > 0
    assert new[0]["role"] == "system" and new[0]["content"] == "persona"
    # one digest message right after system, summarizing the dropped middle
    assert new[1]["role"] == "user" and "compacted" in new[1]["content"].lower()
    assert "f0.py" in new[1]["content"]            # files are listed in the digest
    # the tail is preserved verbatim
    assert new[-1] == msgs[-1]
    assert len(new) < len(msgs)


def test_compact_recent_slice_never_starts_on_orphan_tool():
    msgs = [_msg("system", "s")]
    for i in range(8):
        msgs.append(_msg("assistant", "", [_tc("run", "{}")]))
        msgs.append(_msg("tool", "ok"))
    new, _ = cg.compact(msgs, keep_recent=5)
    # the first message after the digest must not be a dangling tool result
    assert new[2]["role"] != "tool"


def test_compact_noop_when_small():
    msgs = [_msg("system", "s"), _msg("user", "hi"), _msg("assistant", "done")]
    new, dropped = cg.compact(msgs, keep_recent=6)
    assert dropped == 0 and new is msgs


def test_guard_budget_and_should_compact():
    g = cg.ContextGuard(1000, reserve=200, threshold=0.8)
    assert g.budget() == 600                       # 1000*0.8 - 200
    small = [_msg("user", "x" * 100)]               # ~25 tokens
    assert g.should_compact(small) is False
    big = [_msg("user", "x" * 4000)]                # ~1000 tokens > 600
    assert g.should_compact(big) is True


def test_guard_disabled_without_limit():
    g = cg.ContextGuard(None)
    assert g.should_compact([_msg("user", "x" * 100000)]) is False
    msgs = [_msg("user", "x")]
    assert g.maybe_compact(msgs) == (msgs, 0)


def test_guard_maybe_compact_runs_when_over_budget():
    g = cg.ContextGuard(400, reserve=50, threshold=0.5, keep_recent=2)  # budget=150
    msgs = [_msg("system", "s")]
    for i in range(12):
        msgs.append(_msg("assistant", "x" * 200, [_tc("write_file", f'{{"path":"f{i}.py"}}')]))
        msgs.append(_msg("tool", "ok"))
    new, dropped = g.maybe_compact(msgs)
    assert dropped > 0 and len(new) < len(msgs)
    assert cg.estimate_tokens(new) < cg.estimate_tokens(msgs)
