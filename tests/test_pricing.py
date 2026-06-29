"""Tests for cost estimates: per-model rates, local = free, and usage cost rollup."""

from __future__ import annotations

from harness import pricing, usage


def test_rate_by_model_substring():
    assert pricing.rate_for("claude-opus-4-8") == 30.0
    assert pricing.rate_for("sonnet") == 6.0
    assert pricing.rate_for("gpt-4o") == 5.0
    assert pricing.rate_for("gpt-4o-mini") == 0.30


def test_local_engines_are_free():
    assert pricing.rate_for("qwen2.5-coder", "local") == 0.0
    assert pricing.rate_for("anything", "ollama") == 0.0
    assert pricing.estimate_usd(1_000_000, "qwen", "local") == 0.0


def test_engine_default_when_no_model():
    assert pricing.rate_for("", "claude-cli") == 6.0   # sonnet-class default
    assert pricing.rate_for("", "openai") == 5.0


def test_estimate_usd():
    # 1M sonnet tokens ≈ $6
    assert pricing.estimate_usd(1_000_000, "sonnet") == 6.0
    assert pricing.estimate_usd(500_000, "claude-opus") == 15.0
    assert pricing.estimate_usd("bad", "sonnet") == 0.0


def test_summary_includes_cost():
    entries = [
        {"name": "a", "tokens": 1_000_000, "model": "sonnet", "engine": "claude-cli"},
        {"name": "a", "tokens": 1_000_000, "model": "qwen", "engine": "local"},  # free
        {"name": "b", "tokens": 2_000_000, "model": "gpt-4o", "engine": "openai"},
    ]
    s = usage.summary(entries, cost_of=pricing.cost_of)
    assert s["cost"] == 16.0          # 6 + 0 + 10
    by = {p["name"]: p for p in s["projects"]}
    assert by["a"]["cost"] == 6.0 and by["b"]["cost"] == 10.0


def test_summary_without_cost_fn_is_zero():
    s = usage.summary([{"name": "x", "tokens": 100}])
    assert s["cost"] == 0.0
