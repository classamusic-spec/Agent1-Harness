"""Context-window guard + deterministic history compaction.

Small local models have small context windows (a 32k GLM, an 8k base model). As a
build runs, the message history grows — file contents, tool results, repair logs —
and once it overflows the window the server silently truncates the *front* of the
prompt (the system persona + the task), and the model derails: it forgets the goal,
re-does work, or loops. Because the models picker already tells us each model's real
context length, we can watch the running prompt size and compact the history *before*
it overflows.

Compaction here is deterministic (no extra LLM call): keep the system message and the
most recent turns verbatim, and fold everything in between into one compact digest
(which tools ran, which files were written). Stdlib only; pure + unit-tested.
"""

from __future__ import annotations

import json

from harness.meter import approx_tokens

# Per-message overhead (role tags, delimiters) the server adds around each message.
_MSG_OVERHEAD = 4


def _content_text(content) -> str:
    """Flatten a message's content (string or OpenAI vision list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    out.append("[image]")  # images aren't text tokens; a small placeholder
        return " ".join(out)
    return str(content or "")


def estimate_message_tokens(msg: dict) -> int:
    n = approx_tokens(_content_text(msg.get("content")))
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        n += approx_tokens(str(fn.get("name", ""))) + approx_tokens(str(fn.get("arguments", "")))
    return n + _MSG_OVERHEAD


def estimate_tokens(messages: list[dict]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def _paths_in(msg: dict) -> list[str]:
    """Best-effort: file paths a tool call touched (for the digest)."""
    paths = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(args, dict):
            for key in ("path", "file", "filename"):
                if isinstance(args.get(key), str):
                    paths.append(args[key])
    return paths


def _safe_recent_start(messages: list[dict], keep_recent: int) -> int:
    """Index where the kept-recent slice should begin, never starting on an orphan
    `tool` result (whose matching assistant tool_calls would be dropped)."""
    start = max(1, len(messages) - keep_recent)
    while start < len(messages) and messages[start].get("role") == "tool":
        start += 1
    return start


def _digest(dropped: list[dict]) -> str:
    tools, files = [], []
    for m in dropped:
        for tc in (m.get("tool_calls") or []):
            name = (tc.get("function") or {}).get("name")
            if name:
                tools.append(name)
        files.extend(_paths_in(m))
    seen, uniq_files = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq_files.append(f)
    lines = [f"[Earlier context compacted to save the window — {len(dropped)} messages.]"]
    if tools:
        from collections import Counter
        counts = Counter(tools)
        lines.append("Tools run: " + ", ".join(f"{n}×{t}" for t, n in counts.most_common()))
    if uniq_files:
        shown = uniq_files[:20]
        more = "" if len(uniq_files) <= 20 else f" (+{len(uniq_files) - 20} more)"
        lines.append("Files touched: " + ", ".join(shown) + more)
    lines.append("Continue the task; do not redo completed work.")
    return "\n".join(lines)


def compact(messages: list[dict], keep_recent: int = 6) -> tuple[list[dict], int]:
    """Return (new_messages, dropped_count). Keeps the system message + the last
    `keep_recent` turns verbatim and folds the middle into one digest message."""
    if len(messages) <= keep_recent + 2:
        return messages, 0
    head = messages[:1] if messages and messages[0].get("role") == "system" else []
    start = _safe_recent_start(messages, keep_recent)
    dropped = messages[len(head):start]
    if not dropped:
        return messages, 0
    digest = {"role": "user", "content": _digest(dropped)}
    return head + [digest] + messages[start:], len(dropped)


class ContextGuard:
    """Decide when the prompt is getting too big for the model and compact it."""

    def __init__(self, limit: int | None, *, reserve: int = 2048,
                 threshold: float = 0.8, keep_recent: int = 6):
        self.limit = limit or 0
        self.reserve = reserve
        self.threshold = threshold
        self.keep_recent = keep_recent

    def budget(self) -> int:
        """Token ceiling for the prompt (context minus room for the reply)."""
        return max(0, int(self.limit * self.threshold) - self.reserve)

    def should_compact(self, messages: list[dict]) -> bool:
        return bool(self.limit) and estimate_tokens(messages) > self.budget()

    def maybe_compact(self, messages: list[dict]) -> tuple[list[dict], int]:
        """Compact only if over budget. Returns (messages, dropped_count)."""
        if not self.should_compact(messages):
            return messages, 0
        return compact(messages, self.keep_recent)
