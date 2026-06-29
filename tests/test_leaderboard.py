"""Tests for the local-model leaderboard (ranking + per-model run, build injected)."""

from __future__ import annotations

import types

from harness import leaderboard


def _result(ok=True, rounds=1, tokens=1000, elapsed=10.0, stop_reason="passed"):
    return types.SimpleNamespace(ok=ok, rounds=rounds, tokens_used=tokens,
                                 elapsed_seconds=elapsed, stop_reason=stop_reason)


def test_rank_orders_pass_then_rounds_then_speed():
    rows = [
        {"model": "slow-pass", "ok": True, "rounds": 1, "tok_per_sec": 20},
        {"model": "fail", "ok": False, "rounds": 1, "tok_per_sec": 99},
        {"model": "fast-pass", "ok": True, "rounds": 1, "tok_per_sec": 80},
        {"model": "many-rounds", "ok": True, "rounds": 3, "tok_per_sec": 99},
    ]
    ranked = leaderboard.rank(rows)
    assert [r["model"] for r in ranked] == ["fast-pass", "slow-pass", "many-rounds", "fail"]
    assert ranked[0]["rank"] == 1 and ranked[-1]["rank"] == 4


def test_run_model_records_throughput(tmp_path):
    seen = {}

    def fake_build(spec, cfg):
        seen["model"] = cfg.engine.model
        seen["base_url"] = cfg.engine.base_url
        return _result(ok=True, rounds=1, tokens=2000, elapsed=10.0)

    row = leaderboard.run_model("http://localhost:11434/v1", "glm-4.6",
                                root=str(tmp_path), build_fn=fake_build)
    assert row["ok"] is True and row["rounds"] == 1
    assert row["tokens"] == 2000 and row["tok_per_sec"] == 200.0   # 2000/10s
    assert seen["model"] == "glm-4.6"
    assert row["workspace"].startswith(str(tmp_path))


def test_run_model_survives_build_exception(tmp_path):
    def boom(spec, cfg):
        raise RuntimeError("model crashed")

    row = leaderboard.run_model("http://x/v1", "broken", root=str(tmp_path), build_fn=boom)
    assert row["ok"] is False and "RuntimeError" in row["error"]


def test_run_leaderboard_runs_each_and_ranks(tmp_path):
    table = {"a": _result(ok=True, rounds=2, tokens=1000, elapsed=20.0),    # 50 t/s
             "b": _result(ok=True, rounds=1, tokens=1000, elapsed=10.0),    # 100 t/s, 1 round
             "c": _result(ok=False, rounds=5, tokens=500, elapsed=10.0)}
    progress = []
    ranked = leaderboard.run_leaderboard(
        "http://localhost:8000/v1", ["a", "b", "c"], root=str(tmp_path),
        build_fn=lambda spec, cfg: table[cfg.engine.model],
        on_result=lambda r: progress.append(r["model"]))
    assert [r["model"] for r in ranked] == ["b", "a", "c"]   # b: 1 round wins
    assert progress == ["a", "b", "c"]                       # ran in submitted order


def test_bench_spec_is_valid():
    spec = leaderboard.bench_spec()
    assert spec.name == "bench"
    assert spec.checks and "mathutil" in spec.checks[0].command
