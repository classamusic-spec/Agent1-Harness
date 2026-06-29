"""Codex CLI engine: drive OpenAI's `codex` CLI as a build engine.

This lets someone plug in their **ChatGPT / Codex account** — the `codex` CLI signs
in with a ChatGPT subscription (or an OpenAI API key) — and use it to build, the
same way the `claude-cli` engine uses Claude Code. Each `send()` runs one
non-interactive, auto-approved Codex turn inside the workspace; the model edits
files with its own tools, and the workspace on disk is the shared state across turns.

Requires the `codex` CLI on PATH (override with $CODEX_CLI_BIN), authenticated
(`codex login`). Extra args can be set via $CODEX_CLI_ARGS (default: --full-auto).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal

from harness.config import HarnessConfig
from harness.engines.base import Engine
from harness.spec import Spec

_DEFAULT_TIMEOUT = float(os.environ.get("CODEX_CLI_TIMEOUT", "1200"))
# Auto-approve edits + sandboxed writes so the turn runs without interactive prompts.
_DEFAULT_ARGS = os.environ.get("CODEX_CLI_ARGS", "--full-auto").split()


class CodexCLIEngine(Engine):
    def __init__(self, spec: Spec, config: HarnessConfig, system_prompt: str):
        self._spec = spec
        self._config = config
        self._cfg = config.engine
        self._system = system_prompt
        self._bin = os.environ.get("CODEX_CLI_BIN", "codex")
        self._timeout = _DEFAULT_TIMEOUT
        self._proc = None
        self.total_tokens = 0  # codex exec doesn't report token usage to us

    def terminate(self) -> None:
        p = self._proc
        if p is None or p.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                p.kill()
            except ProcessLookupError:
                pass

    async def __aenter__(self) -> "CodexCLIEngine":
        if shutil.which(self._bin) is None:
            raise RuntimeError(
                f"the codex-cli engine needs the '{self._bin}' CLI on PATH "
                f"(install OpenAI Codex and run `codex login`, or set $CODEX_CLI_BIN)")
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def _full_prompt(self, prompt: str) -> str:
        # Codex has no system-prompt flag, so fold the persona into the turn.
        return f"{self._system}\n\n{prompt}" if self._system else prompt

    def _argv(self, prompt: str) -> list[str]:
        argv = [self._bin, "exec", *_DEFAULT_ARGS]
        if self._cfg.model:
            argv += ["-m", self._cfg.model]
        argv += [self._full_prompt(prompt)]
        return argv

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        argv = self._argv(prompt)
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self._config.workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        self._proc = proc
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout
        lines: list[str] = []
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    proc.kill()
                    raise RuntimeError(f"codex CLI timed out after {self._timeout:.0f}s")
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise RuntimeError(f"codex CLI timed out after {self._timeout:.0f}s")
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                lines.append(line)
                if echo and line.strip():
                    print(line, flush=True)
            await proc.wait()
        finally:
            self._proc = None
        if proc.returncode and proc.returncode < 0:
            raise RuntimeError("codex CLI turn was cancelled")
        text = "\n".join(lines).strip()
        if proc.returncode != 0 and not text:
            raise RuntimeError(f"codex CLI failed (exit {proc.returncode})")
        return text
