"""Tests for auto-escalation: stuck local model hands off to a stronger engine."""

from __future__ import annotations

from harness import escalation
from harness.config import DEFAULT_MODEL, EngineConfig, HarnessConfig


def _local():
    return EngineConfig(provider="local", model="glm-4.6",
                        base_url="http://localhost:11434/v1", api_key_env="OPENAI_API_KEY")


def test_choose_fallback_prefers_claude_cli():
    fb = escalation.choose_fallback(_local(), {"claude_cli": True, "anthropic_key": True})
    assert fb.provider == "claude-cli"


def test_choose_fallback_order_anthropic_then_codex_then_openai():
    assert escalation.choose_fallback(_local(), {"anthropic_key": True}).provider == "anthropic"
    assert escalation.choose_fallback(_local(), {"codex_cli": True}).provider == "codex-cli"
    of = escalation.choose_fallback(_local(), {"openai_key": True})
    assert of.provider == "openai" and of.model == "gpt-4o"


def test_choose_fallback_none_when_nothing_available():
    assert escalation.choose_fallback(_local(), {}) is None


def test_choose_fallback_skips_cloud_builders():
    cloud = EngineConfig(provider="anthropic", model=DEFAULT_MODEL)
    assert escalation.choose_fallback(cloud, {"claude_cli": True}) is None


def test_with_auto_fallback_arms_for_local():
    cfg = HarnessConfig(workspace="/tmp/x", engine=_local())
    out = escalation.with_auto_fallback(cfg, probe_fn=lambda: {"anthropic_key": True})
    assert out.fallback_provider == "anthropic"
    # and that flows into the fixer engine the build loop uses
    assert out.fixer_engine().provider == "anthropic"
    assert out.fixer_engine().model == DEFAULT_MODEL


def test_with_auto_fallback_respects_explicit_pin():
    cfg = HarnessConfig(workspace="/tmp/x", engine=_local(),
                        fallback_provider="openai", fallback_model="gpt-4.1")
    out = escalation.with_auto_fallback(cfg, probe_fn=lambda: {"claude_cli": True})
    assert out.fallback_provider == "openai" and out.fallback_model == "gpt-4.1"


def test_with_auto_fallback_noop_when_disabled_or_cloud():
    disabled = HarnessConfig(workspace="/tmp/x", engine=_local(), auto_fallback=False)
    assert escalation.with_auto_fallback(disabled, probe_fn=lambda: {"claude_cli": True}) is disabled
    cloud = HarnessConfig(workspace="/tmp/x", engine=EngineConfig(provider="anthropic"))
    assert escalation.with_auto_fallback(cloud, probe_fn=lambda: {"claude_cli": True}) is cloud


def test_with_auto_fallback_noop_when_nothing_available():
    cfg = HarnessConfig(workspace="/tmp/x", engine=_local())
    assert escalation.with_auto_fallback(cfg, probe_fn=lambda: {}) is cfg


def test_fallback_engine_none_without_config():
    cfg = HarnessConfig(workspace="/tmp/x", engine=_local())
    assert cfg.fallback_engine() is None
    # fixer falls back to the same-backend (optionally stronger) model
    assert cfg.fixer_engine().provider == "local"


def test_fallback_engine_key_env_by_provider():
    cfg = HarnessConfig(workspace="/tmp/x", engine=_local(), fallback_provider="anthropic")
    assert cfg.fallback_engine().api_key_env == "ANTHROPIC_API_KEY"
    cfg2 = HarnessConfig(workspace="/tmp/x", engine=_local(), fallback_provider="claude-cli")
    assert cfg2.fallback_engine().api_key_env == ""


def test_describe():
    assert "Claude Code CLI" in escalation.describe(EngineConfig(provider="claude-cli", model="sonnet"))
    assert "local" in escalation.describe(_local())
