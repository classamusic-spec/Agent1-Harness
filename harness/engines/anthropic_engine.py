"""Anthropic engine: drives the Claude Agent SDK with its built-in tools."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from harness.config import HarnessConfig
from harness.engines.base import Engine
from harness.permissions import make_permission_callback
from harness.spec import Spec
from harness.tools import VERIFY_TOOL, build_tool_server


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    chunks = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            chunks.append(text)
    return "\n".join(chunks)


class AnthropicEngine(Engine):
    def __init__(self, spec: Spec, config: HarnessConfig, system_prompt: str):
        self._spec = spec
        self._config = config
        self._options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=config.workspace,
            model=config.engine.model,
            max_turns=config.max_turns,
            allowed_tools=[*config.allowed_tools, VERIFY_TOOL],
            permission_mode="acceptEdits",
            can_use_tool=make_permission_callback(config.workspace),
            mcp_servers={"harness": build_tool_server(spec)},
        )
        self._client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> "AnthropicEngine":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        assert self._client is not None, "engine not entered"
        await self._client.query(prompt)
        out = []
        async for message in self._client.receive_response():
            text = _extract_text(message)
            if text:
                out.append(text)
                if echo:
                    print(text, flush=True)
        return "\n".join(out)
