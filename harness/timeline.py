"""Build timeline: a structured, per-round record of what the loop actually did.

A build is currently a wall of log text. Everything interesting is tracked —
which checks failed each round, what was repaired, escalations, auto-installs,
review verdicts, tokens — but nothing assembles it. The Timeline collects those
moments as structured events, renders a one-line human summary for the end of
the log ("R1 2 failed (typecheck, tests) → auto-install axios → R2 green"), and
writes `.studio/report.json` into the workspace so the console (or you) can see
exactly how a build went after the fact. Stdlib only; never fails a build.
"""

from __future__ import annotations

import json
import os
import time


class Timeline:
    def __init__(self):
        self.events: list[dict] = []
        self._t0 = time.monotonic()

    def _add(self, kind: str, **data) -> None:
        self.events.append({"kind": kind, "at": round(time.monotonic() - self._t0, 1), **data})

    def round(self, n: int, failures: list[str], tokens: int) -> None:
        self._add("verify", round=n, failures=list(failures), tokens=int(tokens))

    def event(self, kind: str, **data) -> None:
        self._add(kind, **data)

    def finish(self, ok: bool, reason: str, tokens: int, elapsed: float) -> None:
        self._add("finish", ok=bool(ok), reason=reason, tokens=int(tokens),
                  elapsed=round(float(elapsed), 1))

    # ── rendering ─────────────────────────────────────────────────────────
    def summary(self) -> str:
        """One line: what happened, round by round."""
        bits: list[str] = []
        for e in self.events:
            k = e["kind"]
            if k == "verify":
                fails = e.get("failures") or []
                bits.append(f"R{e['round']} green" if not fails
                            else f"R{e['round']} {len(fails)} failed ({', '.join(fails[:3])})")
            elif k == "repair":
                bits.append(f"repair {e.get('n', '')}".strip())
            elif k == "auto-install":
                bits.append("auto-install " + ", ".join(e.get("packages", [])[:3])
                            + ("" if e.get("ok", True) else " (failed)"))
            elif k == "escalation":
                bits.append(f"escalate → {e.get('target', 'fixer')}")
            elif k == "review":
                bits.append("review " + ("approved" if e.get("approved") else "rejected"))
            elif k == "security":
                bits.append(f"security {e.get('blockers', 0)} blocker(s)")
            elif k == "finish":
                bits.append(f"{'✓' if e.get('ok') else '✗'} {e.get('reason', '')} · "
                            f"{e.get('tokens', 0):,} tok · {e.get('elapsed', 0)}s")
        return " → ".join(bits)

    def to_dict(self) -> dict:
        return {"events": list(self.events), "summary": self.summary()}

    def write(self, workspace: str) -> None:
        """Persist to <workspace>/.studio/report.json. Best-effort, never raises."""
        try:
            d = os.path.join(workspace, ".studio")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=1)
        except OSError:
            pass


def load(workspace: str) -> dict:
    """Read a workspace's last build report, or {}."""
    try:
        with open(os.path.join(workspace, ".studio", "report.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
