"""Auto-escalation: when a local model gets stuck, hand off to a stronger engine.

A small local model can stall — the same checks fail round after round and it
can't find the fix. The build loop already detects this (a run of identical
failing rounds) and spawns a fresh "fixer". This module makes that fixer a
genuinely *stronger* engine: if you're building on a local model and have a
Claude CLI, an API key, or a ChatGPT/Codex login available, the stuck workspace
(files already on disk) is handed off to that engine for a turn, then handed
back. You keep the work done so far and don't lose the session to a model that
was never going to converge.

Only escalates *from* a local model — cloud builders are already strong.
"""

from __future__ import annotations

import dataclasses

from harness.config import DEFAULT_MODEL, EngineConfig, HarnessConfig


def choose_fallback(builder: EngineConfig, probe: dict) -> EngineConfig | None:
    """Pick the strongest available engine to escalate a stuck local build to."""
    if builder.provider != "local":
        return None  # cloud/CLI builders are already strong — nothing stronger to add
    if probe.get("claude_cli"):
        return EngineConfig(provider="claude-cli", model="sonnet", api_key_env="")
    if probe.get("anthropic_key"):
        return EngineConfig(provider="anthropic", model=DEFAULT_MODEL,
                            api_key_env="ANTHROPIC_API_KEY")
    if probe.get("codex_cli"):
        return EngineConfig(provider="codex-cli", model="", api_key_env="")
    if probe.get("openai_key"):
        return EngineConfig(provider="openai", model="gpt-4o",
                            base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY")
    return None


def describe(engine: EngineConfig) -> str:
    label = {"claude-cli": "Claude Code CLI", "anthropic": "Anthropic API",
             "codex-cli": "Codex CLI", "openai": "OpenAI API",
             "local": "local model"}.get(engine.provider, engine.provider)
    return f"{label}" + (f" ({engine.model})" if engine.model else "")


def with_auto_fallback(config: HarnessConfig, *, echo: bool = False,
                       probe_fn=None) -> HarnessConfig:
    """Arm an auto-fallback engine on `config` when the builder is local, none is
    pinned, and a stronger engine is available. Returns config unchanged otherwise."""
    if not getattr(config, "auto_fallback", True):
        return config
    if config.fallback_provider or config.fallback_model:
        return config  # an explicit fallback was pinned — respect it
    if config.engine.provider != "local":
        return config
    if probe_fn is None:
        from harness import doctor

        def probe_fn():
            # choose_fallback only needs CLIs + keys — skip doctor.probe()'s
            # local-server HTTP sweep (up to ~6s of timeouts at build start).
            return {
                "claude_cli": doctor.detect_claude_cli(),
                "codex_cli": doctor.detect_codex_cli(),
                "anthropic_key": doctor.detect_anthropic_key(),
                "openai_key": doctor.detect_openai_key(),
            }
    try:
        probe = probe_fn()
    except Exception:
        return config
    fb = choose_fallback(config.engine, probe)
    if fb is None:
        return config
    if echo:
        print(f"  ⤴ auto-fallback armed: if the local model stalls, hand off to "
              f"{describe(fb)} (the workspace is kept)", flush=True)
    return dataclasses.replace(
        config, fallback_provider=fb.provider, fallback_model=fb.model,
        fallback_base_url=fb.base_url, fallback_api_key_env=fb.api_key_env)
