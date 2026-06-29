"""Cost / usage tracking — aggregate tokens + time across all builds.

Every completed build/iterate appends one line to a JSONL log; the dashboard reads
it back and summarizes totals, per-project, and recent runs. Stdlib only; the log is
append-only so concurrent builds don't clobber each other.
"""

from __future__ import annotations

import json
import os


def record(path: str, entry: dict) -> None:
    """Append one usage entry (best-effort; never raises into the caller)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def load(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def summary(entries: list[dict], cost_of=None) -> dict:
    total_tokens = sum(_int(e.get("tokens")) for e in entries)
    total_seconds = sum(_float(e.get("elapsed")) for e in entries)
    passed = sum(1 for e in entries if e.get("status") == "passed")
    failed = sum(1 for e in entries if e.get("status") == "failed")
    total_cost = sum(float(cost_of(e) or 0) for e in entries) if cost_of else 0.0

    by_project: dict[str, dict] = {}
    for e in entries:
        name = str(e.get("name") or "?")
        p = by_project.setdefault(name, {"name": name, "runs": 0, "tokens": 0, "seconds": 0.0, "cost": 0.0})
        p["runs"] += 1
        p["tokens"] += _int(e.get("tokens"))
        p["seconds"] += _float(e.get("elapsed"))
        if cost_of:
            p["cost"] += float(cost_of(e) or 0)
    projects = sorted(by_project.values(), key=lambda p: p["tokens"], reverse=True)
    for p in projects:
        p["seconds"] = round(p["seconds"], 1)
        p["cost"] = round(p["cost"], 2)

    by_day: dict[str, int] = {}
    for e in entries:
        day = str(e.get("ts") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + _int(e.get("tokens"))
    days = [{"day": d, "tokens": t} for d, t in sorted(by_day.items())][-14:]

    recent = list(reversed(entries))[:12]
    return {
        "runs": len(entries),
        "tokens": total_tokens,
        "seconds": round(total_seconds, 1),
        "cost": round(total_cost, 2),
        "passed": passed,
        "failed": failed,
        "projects": projects[:10],
        "by_day": days,
        "recent": recent,
    }
