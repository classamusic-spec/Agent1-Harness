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


def make_engine(spec: Spec, config: HarnessConfig) -> Engine:
    """Construct the engine selected by `config.engine.provider`."""
    sys_prompt = system_prompt(spec.kind)
    provider = config.engine.provider

    if provider == "anthropic":
        from harness.engines.anthropic_engine import AnthropicEngine

        return AnthropicEngine(spec, config, sys_prompt)

    if provider == "local":
        from harness.engines.local_engine import LocalEngine

        return LocalEngine(spec, config, sys_prompt)

    raise ValueError(f"unknown engine provider: {provider!r} (expected 'anthropic' or 'local')")
