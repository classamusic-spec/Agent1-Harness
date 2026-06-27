"""Harness configuration, engine selection, and bounded-autonomy limits."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Default model for the Anthropic engine. The Claude Agent SDK accepts full
# model IDs as well as the short aliases "opus" / "sonnet" / "haiku".
DEFAULT_MODEL = "claude-opus-4-8"

# Default endpoint for the local engine (Ollama). LM Studio uses :1234, vLLM
# and llama.cpp vary — override with --base-url.
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

# Built-in SDK tools the Anthropic engine may use. Scoped to the workspace via
# `cwd` and gated by the permission callback. The custom verify tool is added
# by name at runtime.
DEFAULT_ALLOWED_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]


@dataclass
class EngineConfig:
    """Which model backend to use and how to reach it."""

    provider: str = "anthropic"  # "anthropic" | "local"
    model: str = DEFAULT_MODEL
    base_url: str | None = None  # local only
    # Env var to read the API key from. Local servers usually ignore it; the
    # OpenAI client just needs a non-empty string.
    api_key_env: str = "ANTHROPIC_API_KEY"
    temperature: float = 0.2

    def resolved_api_key(self) -> str:
        return os.environ.get(self.api_key_env) or "local"


@dataclass
class HarnessConfig:
    workspace: str
    engine: EngineConfig = field(default_factory=EngineConfig)
    # Bounded autonomy: repair rounds after the initial build attempt.
    max_repairs: int = 4
    # Cap on agentic turns within a single engine.send() call.
    max_turns: int = 80
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    stop_on_failure: bool = True

    # Isolation: "directory" (default) or "worktree" (git worktree off base_repo).
    isolation: str = "directory"
    base_repo: str | None = None
    keep_workspace: bool = True

    # Reviewer / Sentry second gate.
    enable_review: bool = False
    review_focus: str = "quality"  # "quality" | "bugs"
    reviewer_model: str | None = None  # defaults to the builder model

    # Learning memory.
    learn: bool = False
    memory_path: str | None = None  # JSONL lesson store
    reflect: bool = True  # when learning, ask the model to distill reusable lessons

    # Loop convergence guards.
    stall_limit: int = 3  # stop after N consecutive identical failing rounds
    deadline_seconds: float | None = None  # overall wall-clock budget (None = unlimited)

    def reviewer_engine(self) -> EngineConfig:
        """Engine config for the reviewer (same backend, optional model override)."""
        return EngineConfig(
            provider=self.engine.provider,
            model=self.reviewer_model or self.engine.model,
            base_url=self.engine.base_url,
            api_key_env=self.engine.api_key_env,
            temperature=self.engine.temperature,
        )
