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

    # Workspace isolation: "directory" (default) or "worktree" (git worktree).
    isolation: str = "directory"
    base_repo: str | None = None
    keep_workspace: bool = True

    # Exec sandbox: where commands run — "host" (default) or "docker".
    exec_sandbox: str = "host"
    docker_image: str = "python:3.12-slim"

    # Test-first: derive the verification suite from the spec before building.
    test_first: bool = False

    # Human-in-the-loop approval gates.
    approve_plan: bool = False   # sign off on the test-first suite before building
    approve_build: bool = False  # sign off on the finished build before accepting

    # Token budget (wall-clock budget is deadline_seconds above).
    max_tokens_budget: int | None = None

    # Reviewer / Sentry second gate.
    enable_review: bool = False
    review_focus: str = "quality"  # "quality" | "bugs" | "a11y"
    review_panel: list[str] = field(default_factory=list)  # e.g. ["quality","bugs","a11y"] -> parallel panel
    reviewer_model: str | None = None  # defaults to the builder model

    # Learning memory.
    learn: bool = False
    memory_path: str | None = None  # JSONL lesson store
    reflect: bool = True  # when learning, ask the model to distill reusable lessons

    # Loop convergence guards.
    stall_limit: int = 3  # consecutive identical failing rounds before escalate/stop
    deadline_seconds: float | None = None  # overall wall-clock budget (None = unlimited)
    diff_aware: bool = True  # include a diff of the last change in repair prompts
    max_escalations: int = 1  # fresh-fixer attempts on a stall before giving up
    escalation_model: str | None = None  # stronger model for the fixer (default: builder model)

    def reviewer_engine(self) -> EngineConfig:
        """Engine config for the reviewer (same backend, optional model override)."""
        return EngineConfig(
            provider=self.engine.provider,
            model=self.reviewer_model or self.engine.model,
            base_url=self.engine.base_url,
            api_key_env=self.engine.api_key_env,
            temperature=self.engine.temperature,
        )

    def fixer_engine(self) -> EngineConfig:
        """Engine config for the escalation fixer (optional stronger model)."""
        return EngineConfig(
            provider=self.engine.provider,
            model=self.escalation_model or self.engine.model,
            base_url=self.engine.base_url,
            api_key_env=self.engine.api_key_env,
            temperature=self.engine.temperature,
        )
