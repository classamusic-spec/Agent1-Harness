"""Tests for the cost/usage tracking: append log + aggregation."""

from __future__ import annotations

from harness import usage


def test_record_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "u.jsonl")
    usage.record(path, {"name": "a", "tokens": 100, "elapsed": 5, "status": "passed"})
    usage.record(path, {"name": "b", "tokens": 50, "elapsed": 3, "status": "failed"})
    entries = usage.load(path)
    assert len(entries) == 2 and entries[0]["name"] == "a"


def test_load_missing_is_empty(tmp_path):
    assert usage.load(str(tmp_path / "nope.jsonl")) == []


def test_summary_aggregates_totals_and_projects():
    entries = [
        {"ts": "2026-06-28 10:00", "name": "app1", "tokens": 100, "elapsed": 5, "status": "passed"},
        {"ts": "2026-06-28 11:00", "name": "app1", "tokens": 200, "elapsed": 7, "status": "passed"},
        {"ts": "2026-06-29 09:00", "name": "app2", "tokens": 50, "elapsed": 2, "status": "failed"},
    ]
    s = usage.summary(entries)
    assert s["runs"] == 3
    assert s["tokens"] == 350 and s["seconds"] == 14.0
    assert s["passed"] == 2 and s["failed"] == 1
    assert s["projects"][0]["name"] == "app1" and s["projects"][0]["tokens"] == 300
    assert s["projects"][0]["runs"] == 2
    # tokens grouped by day
    days = {d["day"]: d["tokens"] for d in s["by_day"]}
    assert days["2026-06-28"] == 300 and days["2026-06-29"] == 50
    # recent is newest-first
    assert s["recent"][0]["name"] == "app2"


def test_summary_empty():
    s = usage.summary([])
    assert s["runs"] == 0 and s["tokens"] == 0 and s["projects"] == [] and s["by_day"] == []


def test_summary_tolerates_missing_fields():
    s = usage.summary([{"name": "x"}, {"tokens": "bad"}])  # missing/odd fields don't crash
    assert s["runs"] == 2 and s["tokens"] == 0
