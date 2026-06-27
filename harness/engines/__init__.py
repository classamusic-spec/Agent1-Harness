"""Pluggable execution engines.

An Engine runs the agent for one build session. The harness loop in
`harness.agent` is engine-agnostic: it sends a build prompt, runs the
deterministic verification gate, and sends repair prompts — without caring
whether the model behind the engine is Claude or a local LLM.
"""

from harness.engines.base import Engine, make_engine

__all__ = ["Engine", "make_engine"]
