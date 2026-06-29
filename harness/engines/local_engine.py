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
        # Attach a reference image to the first turn iff the coder is multimodal.
        self._pending_image = (
            config.reference_image
            if config.coder_multimodal and config.reference_image else None
        )

    def _user_message(self, prompt: str) -> dict:
        """Build the user turn, attaching the reference image once if the model can see."""
        if not self._pending_image:
            return {"role": "user", "content": prompt}
        import os
        from harness import vision
        img = self._pending_image
        self._pending_image = None  # only on the first turn
        if not os.path.isfile(img):
            return {"role": "user", "content": prompt}
        with open(img, "rb") as fh:
            url = vision.to_data_url(fh.read(), vision.mime_for(img))
        return {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]}

    async def __aenter__(self) -> "LocalEngine":
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "the local engine needs the 'openai' package: pip install 'agent1-harness[local]'"
            ) from exc

        cfg = self._engine_cfg
        # A generous timeout: big local models (GLM / MiniMax) are slow per token.
        timeout = getattr(self._config, "local_timeout", 600.0)
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.resolved_api_key(),
                                   timeout=timeout, max_retries=1)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        assert self._client is not None, "engine not entered"
        self._messages.append(self._user_message(prompt))
        schemas = self._toolbox.schemas()
        produced: list[str] = []
        stream_on = getattr(self._config, "local_stream", True)

        for _ in range(self._config.max_turns):
            if stream_on:
                msg_dict, content = await self._stream_turn(schemas, echo)
            else:
                msg_dict, content = await self._buffered_turn(schemas, echo)
            self._messages.append(msg_dict)
            if content:
                produced.append(content)

            tool_calls = msg_dict.get("tool_calls")
            if not tool_calls:
                break

            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    result = f"ERROR: arguments for {name} were not valid JSON"
                else:
                    result = await asyncio.to_thread(self._toolbox.dispatch, name, args)
                    if echo:
                        print(f"  [tool] {name} -> {result.splitlines()[0] if result else ''}", flush=True)
                self._messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        return "\n".join(produced)

    async def _buffered_turn(self, schemas, echo) -> tuple[dict, str]:
        resp = await self._client.chat.completions.create(
            model=self._engine_cfg.model, messages=self._messages, tools=schemas,
            tool_choice="auto", temperature=self._engine_cfg.temperature)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        msg = resp.choices[0].message
        if echo and msg.content:
            print(msg.content, flush=True)
        return _assistant_to_dict(msg), (msg.content or "")

    async def _stream_turn(self, schemas, echo) -> tuple[dict, str]:
        """One streamed turn: print text deltas live, accumulate tool calls. Falls
        back to a buffered turn if the server rejects streaming."""
        kwargs = dict(model=self._engine_cfg.model, messages=self._messages, tools=schemas,
                      tool_choice="auto", temperature=self._engine_cfg.temperature, stream=True)
        try:
            try:
                stream = await self._client.chat.completions.create(
                    **kwargs, stream_options={"include_usage": True})
            except TypeError:  # older client / server without stream_options
                stream = await self._client.chat.completions.create(**kwargs)
        except Exception:
            return await self._buffered_turn(schemas, echo)  # server can't stream — fall back

        parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        printed_tool = set()
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self.total_tokens += getattr(usage, "total_tokens", 0) or 0
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                parts.append(piece)
                if echo:
                    print(piece, end="", flush=True)
            for tcd in (getattr(delta, "tool_calls", None) or []):
                acc = tool_acc.setdefault(tcd.index, {"id": "", "name": "", "arguments": ""})
                if getattr(tcd, "id", None):
                    acc["id"] = tcd.id
                fn = getattr(tcd, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        acc["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        acc["arguments"] += fn.arguments
                if echo and acc["name"] and tcd.index not in printed_tool:
                    printed_tool.add(tcd.index)
                    print(f"\n  ✎ {acc['name']}", flush=True)
        if echo and parts:
            print("", flush=True)  # newline after the streamed text

        content = "".join(parts)
        msg: dict = {"role": "assistant", "content": content}
        if tool_acc:
            msg["tool_calls"] = [
                {"id": a["id"] or f"call_{i}", "type": "function",
                 "function": {"name": a["name"], "arguments": a["arguments"] or "{}"}}
                for i, a in sorted(tool_acc.items())]
        return msg, content
