"""Harness configuration and bounded-autonomy limits."""

from __future__ import annotations

from dataclasses import dataclass, field

# Default model for the build agent. The Claude Agent SDK accepts full model IDs
# as well as the short aliases "opus" / "sonnet" / "haiku". We default to the
# most capable model for code generation; override with --model.
DEFAULT_MODEL = "claude-opus-4-8"

# Built-in SDK tools the agent is allowed to use while building an app. These
# are scoped to the workspace via `cwd` and further gated by the permission
# callback (see harness.permissions). The custom verification tool is added
# separately by name at runtime.
DEFAULT_ALLOWED_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]


@dataclass
class HarnessConfig:
    workspace: str
    model: str = DEFAULT_MODEL
    # Bounded autonomy: how many repair rounds after the initial build attempt.
    max_repairs: int = 4
    # Per-turn cap on agentic turns inside the SDK loop.
    max_turns: int = 80
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    # Whether to halt the verification suite at the first hard failure.
    stop_on_failure: bool = True
