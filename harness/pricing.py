"""Rough cost estimates for the usage dashboard.

Token usage is logged per build; this maps (model, engine) to a blended USD rate
per million tokens to show an *approximate* spend. Local engines are free ($0).
Rates are deliberately conservative/blended estimates and easy to edit — they are
not billing-accurate (the harness records total tokens, not an input/output split).
"""

from __future__ import annotations

# Blended USD per 1,000,000 tokens (rough). Matched by substring, longest/first win.
_RATES: list[tuple[str, float]] = [
    ("opus", 30.0),
    ("sonnet", 6.0),
    ("haiku", 1.5),
    ("gpt-4o-mini", 0.30),
    ("4o-mini", 0.30),
    ("gpt-4.1-mini", 0.60),
    ("gpt-4.1", 5.0),
    ("gpt-4o", 5.0),
    ("o4-mini", 2.0),
    ("o3-mini", 2.0),
    ("o3", 8.0),
    ("codex", 6.0),
    ("gpt", 5.0),
]

_LOCAL_ENGINES = {"local", "ollama", "lmstudio"}


def rate_for(model: str | None, engine: str | None = None) -> float:
    """Blended $/1M tokens for a model/engine (0 for local servers)."""
    eng = (engine or "").lower()
    if eng in _LOCAL_ENGINES:
        return 0.0
    m = (model or "").lower()
    for key, rate in _RATES:
        if key in m:
            return rate
    # No model hint — fall back to a sensible per-engine default.
    if eng in ("claude-cli", "anthropic"):
        return 6.0   # assume a Sonnet-class model
    if eng in ("openai", "codex-cli"):
        return 5.0
    return 0.0


def estimate_usd(tokens, model: str | None = None, engine: str | None = None) -> float:
    try:
        t = int(tokens or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(t / 1_000_000 * rate_for(model, engine), 4)


def cost_of(entry: dict) -> float:
    """Estimated cost for one usage log entry."""
    return estimate_usd(entry.get("tokens"), entry.get("model"), entry.get("engine"))
