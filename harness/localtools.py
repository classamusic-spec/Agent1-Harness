"""Workspace-confined tools for the local-LLM engine.

The Claude Agent SDK ships its own built-in Read/Write/Edit/Bash tools, but a
local OpenAI-compatible model has none — so we implement an equivalent toolbox
here and expose it as OpenAI function-tool schemas. Every operation is confined
to the workspace and shell commands are screened with the same policy the
Anthropic engine uses (see harness.permissions).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from harness.permissions import is_within, screen_command
from harness.spec import Spec
from harness.verifier import run_suite

_MAX_TOOL_OUTPUT = 6000


class ToolError(Exception):
    pass


def _clip(text: str, limit: int = _MAX_TOOL_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n...[{len(text) - limit} chars truncated]"


class ToolBox:
    """Filesystem + shell + verify tools for one workspace."""

    def __init__(self, workspace: str, spec: Spec, runner=None):
        self.workspace = os.path.abspath(workspace)
        self.spec = spec
        if runner is None:
            from harness.sandbox import HostRunner
            runner = HostRunner()
        self.runner = runner
        os.makedirs(self.workspace, exist_ok=True)

    # --- path safety -----------------------------------------------------
    def _resolve(self, path: str) -> str:
        if not path:
            raise ToolError("path is required")
        candidate = path if os.path.isabs(path) else os.path.join(self.workspace, path)
        if not is_within(candidate, self.workspace):
            raise ToolError(f"path escapes the workspace: {path!r}")
        return os.path.realpath(candidate)

    # --- tools -----------------------------------------------------------
    def read_file(self, path: str) -> str:
        p = self._resolve(path)
        if not os.path.isfile(p):
            raise ToolError(f"no such file: {path}")
        return _clip(Path(p).read_text())

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_text(content)
        return f"wrote {len(content)} bytes to {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        p = self._resolve(path)
        if not os.path.isfile(p):
            raise ToolError(f"no such file: {path}")
        text = Path(p).read_text()
        count = text.count(old)
        if count == 0:
            raise ToolError("old text not found; read the file and match exactly")
        if count > 1:
            raise ToolError(f"old text is not unique ({count} matches); add surrounding context")
        Path(p).write_text(text.replace(old, new, 1))
        return f"edited {path}"

    def apply_patch(self, path: str, patch: str) -> str:
        """Apply a unified-diff patch to a file (surgical edit; cheaper than a rewrite)."""
        from harness import patch as patchmod
        p = self._resolve(path)
        original = Path(p).read_text() if os.path.isfile(p) else ""
        if not original and not patchmod.is_creation(patch):
            raise ToolError(f"no such file: {path} (use write_file to create it)")
        try:
            updated = patchmod.apply_patch(original, patch)
        except patchmod.PatchError as exc:
            raise ToolError(f"{exc}. Re-send the hunk with correct surrounding context, "
                            "or use write_file for a full rewrite.") from exc
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_text(updated)
        delta = len(updated.splitlines()) - len(original.splitlines())
        return f"patched {path} ({'+' if delta >= 0 else ''}{delta} lines)"

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not os.path.isdir(p):
            raise ToolError(f"not a directory: {path}")
        entries = sorted(os.listdir(p))
        rel = os.path.relpath(p, self.workspace)
        lines = [("[dir] " if os.path.isdir(os.path.join(p, e)) else "      ") + e for e in entries]
        return f"{rel}/\n" + ("\n".join(lines) if lines else "(empty)")

    def search(self, pattern: str, glob: str = "**/*") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"bad regex: {exc}") from exc
        hits: list[str] = []
        for fp in sorted(Path(self.workspace).glob(glob)):
            if not fp.is_file():
                continue
            try:
                for i, line in enumerate(fp.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{fp.relative_to(self.workspace)}:{i}: {line.strip()}")
                        if len(hits) >= 200:
                            break
            except OSError:
                continue
            if len(hits) >= 200:
                break
        return "\n".join(hits) if hits else "(no matches)"

    def run_bash(self, command: str, timeout: int = 600) -> str:
        reason = screen_command(command)
        if reason:
            raise ToolError(reason)
        try:
            proc = self.runner.run(command, self.workspace, timeout)
        except subprocess.TimeoutExpired:
            raise ToolError(f"command timed out after {timeout}s") from None
        out = f"exit {proc.returncode}\n"
        if proc.stdout:
            out += "stdout:\n" + proc.stdout
        if proc.stderr:
            out += "stderr:\n" + proc.stderr
        return _clip(out)

    def verify(self) -> str:
        report = run_suite(self.spec.checks, stop_on_failure=True, runner=self.runner)
        status = "ALL CHECKS PASSED" if report.ok else "VERIFICATION FAILED"
        return _clip(f"{status}\n\n{report.to_feedback() or '(no checks defined)'}")

    # --- OpenAI tool plumbing -------------------------------------------
    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool by name. Errors are returned as strings (fed back to the model)."""
        try:
            args = args or {}
            if name == "read_file":
                return self.read_file(args["path"])
            if name == "write_file":
                return self.write_file(args["path"], args["content"])
            if name == "edit_file":
                return self.edit_file(args["path"], args["old"], args["new"])
            if name == "apply_patch":
                return self.apply_patch(args["path"], args.get("patch") or args.get("diff", ""))
            if name == "list_dir":
                return self.list_dir(args.get("path", "."))
            if name == "search":
                return self.search(args["pattern"], args.get("glob", "**/*"))
            if name == "run_bash":
                return self.run_bash(args["command"], int(args.get("timeout", 600)))
            if name == "verify":
                return self.verify()
            return f"ERROR: unknown tool {name!r}"
        except KeyError as exc:
            return f"ERROR: missing argument {exc}"
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # never crash the loop on a tool error
            return f"ERROR: {type(exc).__name__}: {exc}"

    def schemas(self) -> list[dict]:
        """OpenAI-format function-tool definitions."""
        def fn(name, desc, props, required):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }

        s = lambda d: {"type": "string", "description": d}  # noqa: E731
        return [
            fn("read_file", "Read a text file from the workspace.", {"path": s("relative path")}, ["path"]),
            fn("write_file", "Create or overwrite a file with the given content.",
               {"path": s("relative path"), "content": s("full file content")}, ["path", "content"]),
            fn("edit_file", "Replace a unique snippet in a file. `old` must occur exactly once.",
               {"path": s("relative path"), "old": s("exact text to replace"), "new": s("replacement text")},
               ["path", "old", "new"]),
            fn("apply_patch", "Apply a unified-diff patch to an existing file — the cheapest "
               "way to make a small change (send only the changed hunks, not the whole file). "
               "Line numbers in @@ headers are ignored; hunks are located by their context "
               "lines, so include a few unchanged lines around each edit.",
               {"path": s("relative path"),
                "patch": s("unified diff: '@@' hunks with ' ' context, '-' removed, '+' added lines")},
               ["path", "patch"]),
            fn("list_dir", "List the contents of a directory.", {"path": s("relative path, default '.'")}, []),
            fn("search", "Regex search across files in the workspace.",
               {"pattern": s("regex"), "glob": s("glob filter, default **/*")}, ["pattern"]),
            fn("run_bash", "Run a shell command in the workspace (screened for safety).",
               {"command": s("shell command")}, ["command"]),
            fn("verify", "Run the verification suite and return the results.", {}, []),
        ]
