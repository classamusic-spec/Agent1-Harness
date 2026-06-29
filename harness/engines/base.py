"""Engine interface and factory."""

from __future__ import annotations

import abc

from harness.config import HarnessConfig
from harness.personas import system_prompt
from harness.spec import Spec


class Engine(abc.ABC):
    """An async build session. Use as an async context manager.

    `send(prompt)` runs a single user turn to completion — the model may make
    many internal tool calls — and returns the assistant's text for the turn.
    """

    @abc.abstractmethod
    async def __aenter__(self) -> "Engine": ...

    @abc.abstractmethod
    async def __aexit__(self, *exc) -> None: ...

    @abc.abstractmethod
    async def send(self, prompt: str, *, echo: bool = True) -> str: ...


def make_engine(spec: Spec, config: HarnessConfig, *, system_prompt_override: str | None = None) -> Engine:
    """Construct the engine selected by `config.engine.provider`.

    `system_prompt_override` lets callers (e.g. the reviewer gate) swap the
    persona for a fresh-context engine instance.
    """
    sys_prompt = system_prompt_override or system_prompt(spec.kind)
    provider = config.engine.provider

    if provider == "anthropic":
        from harness.engines.anthropic_engine import AnthropicEngine

        return AnthropicEngine(spec, config, sys_prompt)

    if provider == "local":
        from harness.engines.local_engine import LocalEngine

        return LocalEngine(spec, config, sys_prompt)

    if provider == "openai":
        # OpenAI's API is OpenAI-compatible — reuse the local engine pointed at it.
        import dataclasses

        from harness.engines.local_engine import LocalEngine

        eng = config.engine
        if not eng.base_url or "openai.com" not in eng.base_url:
            eng = dataclasses.replace(eng, base_url="https://api.openai.com/v1")
        # Default the key env to OpenAI's unless the caller set an OpenAI-specific one.
        if eng.api_key_env in ("", "ANTHROPIC_API_KEY"):
            eng = dataclasses.replace(eng, api_key_env="OPENAI_API_KEY")
        return LocalEngine(spec, dataclasses.replace(config, engine=eng), sys_prompt)

    if provider in ("claude-cli", "claude-code"):
        from harness.engines.claude_cli_engine import ClaudeCLIEngine

        return ClaudeCLIEngine(spec, config, sys_prompt)

    if provider in ("codex-cli", "codex"):
        from harness.engines.codex_cli_engine import CodexCLIEngine

        return CodexCLIEngine(spec, config, sys_prompt)

    raise ValueError(
        f"unknown engine provider: {provider!r} "
        f"(expected 'anthropic', 'openai', 'local', 'claude-cli', or 'codex-cli')"
    )
