"""Repo map: a compact symbol-level map of the workspace.

A slow local model that has to `read_file` its way around the project before every
edit burns turns and tokens (and context). Handing it a concise map up front — the
files that exist and the top-level symbols in each — lets it jump straight to the
right file. It's the cheapest big win for iteration speed on local models: one small
block instead of a dozen reads.

The map is bounded (file + symbol caps, byte ceiling) so it never itself blows the
context window. Symbol extraction is regex-based for Python / JS / TS; other source
files are listed with a line count. Stdlib only; pure + unit-tested.
"""

from __future__ import annotations

import os
import re

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".studio", ".pytest_cache",
              ".venv", "venv", "dist", "build", ".next", ".cache", "coverage"}
_SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".html", ".css",
               ".json", ".md", ".go", ".rs", ".rb", ".java", ".sql", ".vue", ".svelte",
               ".yaml", ".yml", ".toml", ".sh"}

_PY_SYM = re.compile(r"^(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
_JS_FN = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")
_JS_CLASS = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_CONST = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
_MD_HEAD = re.compile(r"^#{1,3}\s+(.*)")


def _symbols(ext: str, text: str, cap: int) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        if ext == ".py":
            m = _PY_SYM.match(line)
            if m:
                out.append(f"{'class' if m.group(1) == 'class' else 'def'} {m.group(2)}")
        elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"):
            m = _JS_FN.match(line) or _JS_CLASS.match(line)
            if m:
                kind = "class" if _JS_CLASS.match(line) else "function"
                out.append(f"{kind} {m.group(1)}")
            else:
                mc = _JS_CONST.match(line)
                if mc:
                    out.append(mc.group(1))
        elif ext == ".md":
            m = _MD_HEAD.match(line)
            if m:
                out.append("# " + m.group(1).strip())
        if len(out) >= cap:
            break
    return out


def collect(workspace: str, *, max_files: int = 60, max_symbols: int = 12) -> list[dict]:
    """Return [{path, lines, symbols}] for the workspace's source files, sorted by path."""
    files: list[dict] = []
    for dp, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _SOURCE_EXT:
                continue
            full = os.path.join(dp, fn)
            try:
                text = open(full, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(full, workspace)
            files.append({"path": rel, "lines": text.count("\n") + 1,
                          "symbols": _symbols(ext, text, max_symbols)})
    files.sort(key=lambda f: f["path"])
    return files[:max_files]


def render(workspace: str, *, max_files: int = 60, max_symbols: int = 12,
           max_bytes: int = 6000) -> str:
    """A compact text repo map, or '' if the workspace has no source files."""
    files = collect(workspace, max_files=max_files, max_symbols=max_symbols)
    if not files:
        return ""
    lines = [f"## Repo map ({len(files)} file(s)) — jump straight to the right file; "
             "read only what you need to edit."]
    for f in files:
        head = f"- {f['path']} ({f['lines']} ln)"
        if f["symbols"]:
            head += ": " + ", ".join(f["symbols"])
        lines.append(head)
    out = "\n".join(lines)
    if len(out) > max_bytes:
        out = out[:max_bytes].rsplit("\n", 1)[0] + "\n- …(map truncated)"
    return out
