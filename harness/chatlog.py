"""Per-project conversation memory for Studio iterations.

Every "Send change" used to be a cold start: a fresh engine turn with no idea what
you asked three changes ago, what approach was taken, or what just failed. This
keeps a rolling conversation log per project (`.studio/chat.jsonl`) — each iterate
appends the request and its outcome — and renders a compact digest that is injected
into the next iterate's prompt. Engine-agnostic (works for claude-cli, local, and
API engines alike) and bounded, so it stays small enough for a 32k local model.

Stdlib only; pure functions over JSONL; fully unit-tested.
"""

from __future__ import annotations

import json
import os
import time

_MAX_KEEP = 200          # hard cap on stored turns (file is rewritten beyond this)
_RECENT = 10             # turns rendered verbatim in the digest


def _path(workspace: str) -> str:
    return os.path.join(workspace, ".studio", "chat.jsonl")


def append(workspace: str, role: str, text: str, **extra) -> None:
    """Record one conversation turn. Never raises — memory is a convenience."""
    text = (text or "").strip()
    if not text:
        return
    entry = {"role": role, "text": text[:600], "ts": int(time.time()), **extra}
    try:
        os.makedirs(os.path.dirname(_path(workspace)), exist_ok=True)
        with open(_path(workspace), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def history(workspace: str, limit: int = _MAX_KEEP) -> list[dict]:
    """The stored turns, oldest first. Corrupt lines are skipped."""
    out: list[dict] = []
    try:
        with open(_path(workspace), encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("text"):
                    out.append(e)
    except OSError:
        return []
    return out[-limit:]


def record_change(workspace: str, instruction: str, status: str,
                  detail: str = "") -> None:
    """Convenience: log a change request + its outcome in one call."""
    append(workspace, "user", instruction.splitlines()[0])
    outcome = status + (f" — {detail}" if detail else "")
    append(workspace, "result", outcome)


def render(workspace: str, *, recent: int = _RECENT, max_chars: int = 1800) -> str:
    """A compact '## Conversation so far' block for the next iterate's prompt,
    or '' when there is no history."""
    turns = history(workspace)
    if not turns:
        return ""
    older = len(turns) - recent
    lines = ["## Conversation so far (newest last — don't undo earlier requests)"]
    if older > 0:
        lines.append(f"[{older} earlier turn(s) omitted]")
    for e in turns[-recent:]:
        tag = {"user": "user asked", "result": "outcome"}.get(e.get("role"), e.get("role"))
        lines.append(f"- {tag}: {e['text']}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rsplit("\n", 1)[0] + "\n- …(truncated)"
    return out
