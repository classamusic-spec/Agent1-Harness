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
import signal

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


def _render_tool(block: dict) -> str | None:
    """A concise live line for a tool_use event (file edit, command, read)."""
    name = block.get("name") or ""
    inp = block.get("input") or {}
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        f = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"  ✎ {name} {os.path.basename(str(f)) or f}".rstrip()
    if name == "Bash":
        cmd = (inp.get("command") or "").strip().splitlines()
        return f"  $ {cmd[0][:120]}" if cmd else "  $ (command)"
    if name == "Read":
        f = inp.get("file_path") or ""
        return f"  · Read {os.path.basename(str(f)) or f}".rstrip()
    if name in ("Grep", "Glob"):
        t = inp.get("pattern") or inp.get("path") or inp.get("file_path") or ""
        return f"  · {name} {t}".rstrip()
    return f"  · {name}" if name else None


class ClaudeCLIEngine(Engine):
    def __init__(self, spec: Spec, config: HarnessConfig, system_prompt: str):
        self._spec = spec
        self._config = config
        self._cfg = config.engine
        self._system = system_prompt
        self._bin = os.environ.get("CLAUDE_CLI_BIN", "claude")
        self._timeout = _DEFAULT_TIMEOUT
        self._proc = None  # live subprocess (for cancellation)
        self.total_tokens = 0
        self.last_cost_usd = 0.0

    def terminate(self) -> None:
        """Kill the in-flight CLI turn (and its children). Safe to call cross-thread."""
        p = self._proc
        if p is None or p.returncode is not None:
            return
        # The turn runs in its own session/process group (start_new_session=True),
        # so kill the whole group to take the CLI and any workers it spawned.
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                p.kill()
            except ProcessLookupError:
                pass

    async def __aenter__(self) -> "ClaudeCLIEngine":
        if shutil.which(self._bin) is None:
            raise RuntimeError(
                f"the claude-cli engine needs the '{self._bin}' CLI on PATH "
                f"(install Claude Code, or set $CLAUDE_CLI_BIN)"
            )
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    # Stream the turn as line-delimited JSON events so the live log shows the
    # agent's text and file edits as they happen. Disable with CLAUDE_CLI_STREAM=0.
    _STREAM = os.environ.get("CLAUDE_CLI_STREAM", "1") != "0"

    def _argv(self, prompt: str) -> list[str]:
        argv = [
            self._bin, "-p", prompt,
            "--permission-mode", "acceptEdits",
            "--allowedTools", *_ALLOWED_TOOLS,
        ]
        argv += (["--output-format", "stream-json", "--verbose"] if self._STREAM
                 else ["--output-format", "json"])
        if self._cfg.model:
            argv += ["--model", self._cfg.model]
        if self._system:
            argv += ["--append-system-prompt", self._system]
        return argv

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        return await (self._send_stream(prompt, echo) if self._STREAM
                      else self._send_buffered(prompt, echo))

    async def _spawn(self, prompt: str):
        proc = await asyncio.create_subprocess_exec(
            *self._argv(prompt),
            cwd=self._config.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group so terminate() can kill children
        )
        self._proc = proc
        return proc

    async def _send_stream(self, prompt: str, echo: bool) -> str:
        proc = await self._spawn(prompt)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout
        err_chunks: list[bytes] = []

        async def drain_err():
            try:
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    err_chunks.append(chunk)
            except Exception:
                pass

        err_task = asyncio.ensure_future(drain_err())
        assistant_parts: list[str] = []
        result_text = ""
        is_error = False
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    proc.kill()
                    raise RuntimeError(f"claude CLI timed out after {self._timeout:.0f}s")
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise RuntimeError(f"claude CLI timed out after {self._timeout:.0f}s")
                if not line:
                    break
                s = line.decode("utf-8", "replace").strip()
                if not s:
                    continue
                try:
                    ev = json.loads(s)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "assistant":
                    for block in (ev.get("message", {}) or {}).get("content", []) or []:
                        if block.get("type") == "text" and block.get("text"):
                            assistant_parts.append(block["text"])
                            if echo:
                                print(block["text"], flush=True)
                        elif block.get("type") == "tool_use" and echo:
                            tl = _render_tool(block)
                            if tl:
                                print(tl, flush=True)
                elif kind == "result":
                    self.total_tokens += _sum_tokens(ev.get("usage") or {})
                    self.last_cost_usd = float(ev.get("total_cost_usd") or 0.0)
                    result_text = str(ev.get("result") or "")
                    is_error = bool(ev.get("is_error"))
            await proc.wait()
        finally:
            self._proc = None
            err_task.cancel()

        if proc.returncode and proc.returncode < 0:
            raise RuntimeError("claude CLI turn was cancelled")
        stderr = b"".join(err_chunks).decode("utf-8", "replace").strip()
        text = result_text or "\n".join(assistant_parts)
        if is_error:
            raise RuntimeError(f"claude CLI returned an error: {text[:500] or stderr[:500]}")
        if proc.returncode != 0 and not text:
            raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {stderr[:500]}")
        return text

    async def _send_buffered(self, prompt: str, echo: bool) -> str:
        proc = await self._spawn(prompt)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude CLI timed out after {self._timeout:.0f}s")
        finally:
            self._proc = None

        if proc.returncode and proc.returncode < 0:
            raise RuntimeError("claude CLI turn was cancelled")
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
