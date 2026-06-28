"""Claude Code (CLI) engine: drive the installed `claude` CLI as a build engine.

This lets the harness "plug in Claude Code" itself — no API key or SDK needed,
just an authenticated Claude Code CLI on PATH. Each `send()` runs one
non-interactive, agentic turn (`claude -p …`) inside the workspace; the model
edits files directly with its own tools. The workspace on disk is the shared
state across turns, so the engine is stateless between calls by design (the
model re-reads what it needs), which also avoids any nested-session coupling.

Requires: the `claude` CLI on PATH (override with $CLAUDE_CLI_BIN), authenticated.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil

from harness.config import HarnessConfig
from harness.engines.base import Engine
from harness.spec import Spec

# Tools the builder is allowed to use without a permission prompt. `acceptEdits`
# auto-accepts file writes; pre-allowing these covers reads/search/commands too.
_ALLOWED_TOOLS = ["Write", "Edit", "MultiEdit", "Read", "Bash", "Glob", "Grep", "NotebookEdit"]
_DEFAULT_TIMEOUT = float(os.environ.get("CLAUDE_CLI_TIMEOUT", "1200"))


def _sum_tokens(usage: dict) -> int:
    """Total tokens processed in a turn (prompt + completion + cache)."""
    if not isinstance(usage, dict):
        return 0
    keys = (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    )
    return sum(int(usage.get(k) or 0) for k in keys)


class ClaudeCLIEngine(Engine):
    def __init__(self, spec: Spec, config: HarnessConfig, system_prompt: str):
        self._spec = spec
        self._config = config
        self._cfg = config.engine
        self._system = system_prompt
        self._bin = os.environ.get("CLAUDE_CLI_BIN", "claude")
        self._timeout = _DEFAULT_TIMEOUT
        self.total_tokens = 0
        self.last_cost_usd = 0.0

    async def __aenter__(self) -> "ClaudeCLIEngine":
        if shutil.which(self._bin) is None:
            raise RuntimeError(
                f"the claude-cli engine needs the '{self._bin}' CLI on PATH "
                f"(install Claude Code, or set $CLAUDE_CLI_BIN)"
            )
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def _argv(self, prompt: str) -> list[str]:
        argv = [
            self._bin, "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", *_ALLOWED_TOOLS,
            "--output-format", "json",
        ]
        if self._cfg.model:
            argv += ["--model", self._cfg.model]
        if self._system:
            argv += ["--append-system-prompt", self._system]
        return argv

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        argv = self._argv(prompt)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._config.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude CLI timed out after {self._timeout:.0f}s")

        stdout = out.decode("utf-8", "replace").strip()
        stderr = err.decode("utf-8", "replace").strip()
        if proc.returncode != 0 and not stdout:
            raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {stderr[:500]}")

        text = stdout
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            self.total_tokens += _sum_tokens(data.get("usage") or {})
            self.last_cost_usd = float(data.get("total_cost_usd") or 0.0)
            text = str(data.get("result") or "")
            if data.get("is_error"):
                raise RuntimeError(f"claude CLI returned an error: {text[:500] or stderr[:500]}")

        if echo and text:
            print(text, flush=True)
        return text
