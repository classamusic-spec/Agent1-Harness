"""Workspace snapshots and unified diffs.

Used for diff-aware repair: between repair turns we snapshot the workspace and
show the model a unified diff of what *it* just changed, so the next repair
targets the regression instead of rewriting blindly.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
_MAX_FILES = 400
_MAX_BYTES = 200_000


def snapshot(workspace: str) -> dict[str, str]:
    """Map relative path -> text content for files under the workspace."""
    out: dict[str, str] = {}
    base = os.path.abspath(workspace)
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            p = Path(root) / name
            rel = os.path.relpath(p, base)
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if len(data) > _MAX_BYTES:
                out[rel] = "<large file omitted>"
            else:
                try:
                    out[rel] = data.decode("utf-8")
                except UnicodeDecodeError:
                    out[rel] = "<binary file>"
            if len(out) >= _MAX_FILES:
                return out
    return out


def diff_snapshots(before: dict[str, str] | None, after: dict[str, str] | None, max_chars: int = 6000) -> str:
    """Unified diff between two snapshots (added/removed/changed files)."""
    before = before or {}
    after = after or {}
    chunks: list[str] = []
    for key in sorted(set(before) | set(after)):
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        b_lines = (b or "").splitlines()
        a_lines = (a or "").splitlines()
        frm = "/dev/null" if b is None else f"a/{key}"
        to = "/dev/null" if a is None else f"b/{key}"
        diff = difflib.unified_diff(b_lines, a_lines, fromfile=frm, tofile=to, lineterm="", n=2)
        chunks.append("\n".join(diff))
    text = "\n".join(c for c in chunks if c.strip())
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[diff truncated, {len(text) - max_chars} more chars]"
    return text
