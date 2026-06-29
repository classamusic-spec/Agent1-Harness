"""Local-model leaderboard: benchmark your downloaded models on one real task.

The models picker tells you a model's context length and *likely* tool-calling,
but the only way to know which local model actually builds well on your hardware
is to run one. This runs a small, deterministic build task against each model and
ranks them by what matters: did it pass, in how many rounds, and how fast
(tokens/sec). Turns "guess by name" into "ranked by real results on this machine."

The benchmark task is tiny on purpose (implement two functions, checked by a
self-contained assertion) so a healthy coder model finishes in one round. The
build function is injectable so the ranking logic is unit-tested offline.
"""

from __future__ import annotations

import os
import re
import tempfile
import time

# A small, self-contained task: no seeded test file, no frameworks, deterministic
# pass/fail via a one-line assertion. Good signal for reliability + throughput.
BENCH_SPEC = {
    "name": "bench",
    "kind": "backend",
    "language": "python",
    "description": ("Create a single file mathutil.py defining two functions: "
                    "add(a, b) returning a + b, and is_even(n) returning True iff n "
                    "is even. Standard library only; no other files."),
    "verification": [{
        "name": "behaves",
        "command": ("python -c \"from mathutil import add, is_even; "
                    "assert add(2, 3) == 5; assert is_even(4) and not is_even(3); "
                    "print('PASS')\""),
    }],
}


def bench_spec():
    from harness.spec import parse_spec
    return parse_spec(dict(BENCH_SPEC))


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:40] or "model"


def _default_build(spec, config):
    import asyncio
    from harness.agent import build
    return asyncio.run(build(spec, config, echo=False))


def run_model(base_url: str, model: str, *, root: str | None = None,
              build_fn=_default_build, max_repairs: int = 2) -> dict:
    """Build the benchmark task once with `model`; return a result row."""
    from harness.config import EngineConfig, HarnessConfig
    root = root or tempfile.gettempdir()
    ws = tempfile.mkdtemp(prefix=f"bench-{_safe(model)}-", dir=root)
    cfg = HarnessConfig(
        workspace=ws,
        engine=EngineConfig(provider="local", model=model, base_url=base_url,
                            api_key_env="OPENAI_API_KEY"),
        max_repairs=max_repairs,
        local_warmup=True,
    )
    row = {"model": model, "ok": False, "rounds": 0, "tokens": 0,
           "elapsed": 0.0, "tok_per_sec": 0.0, "error": "", "workspace": ws}
    t0 = time.monotonic()
    try:
        result = build_fn(spec_for(cfg), cfg)
    except Exception as exc:  # noqa: BLE001 - one model failing must not stop the rest
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["elapsed"] = round(time.monotonic() - t0, 1)
        return row
    elapsed = getattr(result, "elapsed_seconds", None) or (time.monotonic() - t0)
    tokens = getattr(result, "tokens_used", 0) or 0
    row.update({
        "ok": bool(getattr(result, "ok", False)),
        "rounds": int(getattr(result, "rounds", 0) or 0),
        "tokens": int(tokens),
        "elapsed": round(elapsed, 1),
        "tok_per_sec": round(tokens / elapsed, 1) if elapsed else 0.0,
        "error": "" if getattr(result, "ok", False) else getattr(result, "stop_reason", ""),
    })
    return row


def spec_for(config):
    """The benchmark spec (separate hook so callers/tests can swap the task)."""
    return bench_spec()


def rank(results: list[dict]) -> list[dict]:
    """Best first: passing models, then fewest rounds, then fastest tokens/sec."""
    ordered = sorted(
        results,
        key=lambda r: (0 if r.get("ok") else 1, r.get("rounds", 99),
                       -(r.get("tok_per_sec") or 0)))
    for i, r in enumerate(ordered, 1):
        r["rank"] = i
    return ordered


def run_leaderboard(base_url: str, models: list[str], *, root: str | None = None,
                    build_fn=_default_build, on_result=None) -> list[dict]:
    """Benchmark every model in turn and return the ranked rows."""
    results = []
    for model in models:
        row = run_model(base_url, model, root=root, build_fn=build_fn)
        results.append(row)
        if on_result:
            try:
                on_result(row)
            except Exception:
                pass
    return rank(results)
