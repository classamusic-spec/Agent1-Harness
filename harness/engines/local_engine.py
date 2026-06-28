"""Local engine: an agentic tool-calling loop over any OpenAI-compatible server.

Works with Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1),
vLLM, llama.cpp's server, text-generation-webui, etc. The model must support
OpenAI-style tool calling for the file/bash tools to work.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from harness.config import HarnessConfig
from harness.engines.base import Engine
from harness.localtools import ToolBox
from harness.sandbox import build_runner
from harness.spec import Spec


def _assistant_to_dict(msg: Any) -> dict:
    """Serialize an OpenAI assistant message (with tool calls) back into the history."""
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
            }
            for tc in tool_calls
        ]
    return out


class LocalEngine(Engine):
    def __init__(self, spec: Spec, config: HarnessConfig, system_prompt: str):
        self._spec = spec
        self._config = config
        self._engine_cfg = config.engine
        self._toolbox = ToolBox(config.workspace, spec, runner=build_runner(config))
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._client = None  # created on __aenter__
        self.total_tokens = 0

    async def __aenter__(self) -> "LocalEngine":
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "the local engine needs the 'openai' package: pip install 'agent1-harness[local]'"
            ) from exc

        cfg = self._engine_cfg
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.resolved_api_key())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        assert self._client is not None, "engine not entered"
        self._messages.append({"role": "user", "content": prompt})
        schemas = self._toolbox.schemas()
        produced: list[str] = []

        for _ in range(self._config.max_turns):
            resp = await self._client.chat.completions.create(
                model=self._engine_cfg.model,
                messages=self._messages,
                tools=schemas,
                tool_choice="auto",
                temperature=self._engine_cfg.temperature,
            )
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self.total_tokens += getattr(usage, "total_tokens", 0) or 0

            msg = resp.choices[0].message
            self._messages.append(_assistant_to_dict(msg))

            if msg.content:
                produced.append(msg.content)
                if echo:
                    print(msg.content, flush=True)

            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                break

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = f"ERROR: arguments for {name} were not valid JSON"
                else:
                    result = await asyncio.to_thread(self._toolbox.dispatch, name, args)
                    if echo:
                        print(f"  [tool] {name} -> {result.splitlines()[0] if result else ''}", flush=True)
                self._messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        return "\n".join(produced)
