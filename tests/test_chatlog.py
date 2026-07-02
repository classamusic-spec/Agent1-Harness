"""Tests for per-project conversation memory (Studio iterate context)."""

from __future__ import annotations

from harness import chatlog


def test_append_and_history(tmp_path):
    ws = str(tmp_path)
    chatlog.append(ws, "user", "add a dark mode toggle")
    chatlog.append(ws, "result", "passed")
    turns = chatlog.history(ws)
    assert [t["role"] for t in turns] == ["user", "result"]
    assert turns[0]["text"] == "add a dark mode toggle"


def test_record_change_logs_request_and_outcome(tmp_path):
    ws = str(tmp_path)
    chatlog.record_change(ws, "make the header sticky\nsecond line ignored",
                          "failed", detail="failing: typecheck")
    turns = chatlog.history(ws)
    assert turns[0]["text"] == "make the header sticky"
    assert turns[1]["text"] == "failed — failing: typecheck"


def test_render_digest_recent_verbatim_older_summarized(tmp_path):
    ws = str(tmp_path)
    for i in range(15):
        chatlog.record_change(ws, f"change {i}", "passed")
    out = chatlog.render(ws, recent=6)
    assert out.startswith("## Conversation so far")
    assert "change 14" in out                       # newest kept verbatim
    assert "change 0" not in out                    # oldest folded away
    assert "earlier turn(s) omitted" in out


def test_render_empty_without_history(tmp_path):
    assert chatlog.render(str(tmp_path)) == ""


def test_render_respects_char_budget(tmp_path):
    ws = str(tmp_path)
    for i in range(10):
        chatlog.append(ws, "user", "x" * 400)
    out = chatlog.render(ws, max_chars=600)
    assert len(out) <= 650 and "truncated" in out


def test_history_skips_corrupt_lines(tmp_path):
    ws = str(tmp_path)
    chatlog.append(ws, "user", "good turn")
    with open(tmp_path / ".studio" / "chat.jsonl", "a") as fh:
        fh.write("{not json\n")
    chatlog.append(ws, "user", "another good turn")
    assert [t["text"] for t in chatlog.history(ws)] == ["good turn", "another good turn"]


def test_append_never_raises_on_bad_workspace(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    bad = str(blocker / "nested")          # path under a regular file -> OSError
    chatlog.append(bad, "user", "hi")      # must not raise
    assert chatlog.history(bad) == []
