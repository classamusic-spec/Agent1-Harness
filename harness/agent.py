"""The orchestrator: plan -> implement -> verify -> repair loop.

This is the only module that talks to the Claude Agent SDK. The control flow is
deterministic and lives here in Python; the model is invoked at each step but
never decides when the build is finished — the verification gate does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from harness.config import HarnessConfig
from harness.permissions import make_permission_callback
from harness.prompts import SYSTEM_PROMPT, build_prompt, repair_prompt
from harness.spec import Spec
from harness.tools import VERIFY_TOOL, build_tool_server
from harness.verifier import VerificationReport, run_suite


@dataclass
class BuildResult:
    ok: bool
    attempts: int
    report: VerificationReport | None
    transcript: list[str] = field(default_factory=list)


def _extract_text(message: Any) -> str:
    """Best-effort extraction of human-readable text from an SDK message.

    Message/content classes vary across SDK versions, so we duck-type rather
    than match concrete types: anything with a `.text` attribute on a content
    block is surfaced.
    """
    chunks: list[str] = []
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            chunks.append(text)
    return "\n".join(chunks)


async def _drain(client: ClaudeSDKClient, transcript: list[str], echo: bool) -> None:
    async for message in client.receive_response():
        text = _extract_text(message)
        if text:
            transcript.append(text)
            if echo:
                print(text, flush=True)


def _make_options(spec: Spec, config: HarnessConfig) -> ClaudeAgentOptions:
    server = build_tool_server(spec)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=config.workspace,
        model=config.model,
        max_turns=config.max_turns,
        allowed_tools=[*config.allowed_tools, VERIFY_TOOL],
        permission_mode="acceptEdits",
        can_use_tool=make_permission_callback(config.workspace),
        mcp_servers={"harness": server},
    )


async def build(spec: Spec, config: HarnessConfig, *, echo: bool = True) -> BuildResult:
    """Run the full build-and-verify loop for a spec. Returns a BuildResult."""
    Path(config.workspace).mkdir(parents=True, exist_ok=True)
    os.makedirs(config.workspace, exist_ok=True)

    transcript: list[str] = []
    options = _make_options(spec, config)

    async with ClaudeSDKClient(options=options) as client:
        # Initial build turn.
        await client.query(build_prompt(spec))
        await _drain(client, transcript, echo)

        if not spec.has_verification:
            # Nothing to verify against — return after the build turn.
            return BuildResult(ok=True, attempts=1, report=None, transcript=transcript)

        # Verify, then repair up to max_repairs times.
        for attempt in range(config.max_repairs + 1):
            report = run_suite(spec.checks, stop_on_failure=config.stop_on_failure)
            if echo:
                print(_banner(f"verification (after attempt {attempt + 1})"), flush=True)
                print(report.to_feedback() or "(no checks)", flush=True)

            if report.ok:
                return BuildResult(ok=True, attempts=attempt + 1, report=report, transcript=transcript)

            if attempt == config.max_repairs:
                return BuildResult(ok=False, attempts=attempt + 1, report=report, transcript=transcript)

            await client.query(repair_prompt(report, attempt + 1, config.max_repairs))
            await _drain(client, transcript, echo)

    # Unreachable in practice, but keeps the type checker happy.
    return BuildResult(ok=False, attempts=config.max_repairs + 1, report=None, transcript=transcript)


def _banner(text: str) -> str:
    return f"\n{'=' * 8} {text} {'=' * 8}"
