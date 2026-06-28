"""Milestone planner — decompose a big app into an ordered set of milestones.

For a large full-stack app, one giant implement turn is brittle. The planner asks
the engine for an ordered plan (e.g. schema → API → UI → integration → polish),
each milestone with a goal and optional verification; `agent._build_planned` then
drives each milestone to green (its own build/verify/repair loop, fresh context)
before moving on. Earlier milestones persist in the workspace, so later ones build
on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.config import HarnessConfig
from harness.review import extract_json
from harness.spec import Spec

PLANNER_SYSTEM = (
    "You are a senior software architect. You produce a concise, ordered build plan "
    "for an application — you do NOT write code or create files. Think in vertical, "
    "verifiable increments that each leave the app in a working state."
)


@dataclass
class Milestone:
    title: str
    goal: str
    checks: list[dict] = field(default_factory=list)


def plan_prompt(spec: Spec, max_milestones: int, scaffold_note: str = "") -> str:
    base = (f"\nStarting base (plan milestones that EXTEND this — keep its stack, structure, and "
            f"run command; do not switch frameworks):\n{scaffold_note}\n") if scaffold_note else ""
    return (
        f"Plan the build of this application as an ordered list of milestones.\n\n"
        f"App: **{spec.name}** (kind: {spec.kind}, stack: {spec.language})\n"
        f"Specification:\n{spec.description}\n"
        f"{base}\n"
        f"Rules:\n"
        f"- Between 2 and {max_milestones} milestones, ordered so each builds on the last and "
        f"leaves the app runnable (e.g. data/model → backend API → frontend → integration → polish).\n"
        f"- Each milestone: a short title, a concrete goal, and optional `checks` (shell commands "
        f"that verify it; mark a check `needs_server: true` if it must hit the running app via $APP_URL).\n"
        f"- Do NOT write code or create files. Respond with ONLY this JSON:\n"
        '{"milestones": [{"title": "...", "goal": "...", '
        '"checks": [{"name": "...", "command": "...", "needs_server": false}]}]}'
    )


def parse_plan(text: str, max_milestones: int) -> list[Milestone]:
    data = extract_json(text) or {}
    raw = data.get("milestones") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[Milestone] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        title = str(m.get("title") or m.get("name") or "").strip()
        goal = str(m.get("goal") or m.get("description") or title).strip()
        if not (title or goal):
            continue
        checks = []
        for c in (m.get("checks") or []):
            if isinstance(c, dict) and c.get("name") and c.get("command"):
                checks.append({"name": str(c["name"]), "command": str(c["command"]),
                               "needs_server": bool(c.get("needs_server"))})
        out.append(Milestone(title=title or goal[:40], goal=goal or title, checks=checks))
    return out[:max_milestones]


async def make_plan(spec: Spec, config: HarnessConfig, *, echo: bool = True) -> list[Milestone]:
    """Ask the engine for a milestone plan. Falls back to a single milestone."""
    from harness.engines.base import make_engine
    scaffold_note = ""
    if config.scaffold:
        from harness import scaffolds
        sc = scaffolds.get(config.scaffold)
        if sc:
            scaffold_note = sc.note
    engine = make_engine(spec, config, system_prompt_override=PLANNER_SYSTEM)
    try:
        async with engine:
            text = await engine.send(
                plan_prompt(spec, config.max_milestones, scaffold_note), echo=False)
    except Exception as exc:
        if echo:
            print(f"[planner] planning failed ({type(exc).__name__}: {exc}); building in one pass.",
                  flush=True)
        return [Milestone(title="Build the app", goal=spec.description)]
    plan = parse_plan(text, config.max_milestones)
    if not plan:
        return [Milestone(title="Build the app", goal=spec.description)]
    return plan


def milestone_description(spec: Spec, milestones: list[Milestone], i: int) -> str:
    """The focused implement prompt body for milestone i (with full plan as context)."""
    lines = []
    for j, m in enumerate(milestones):
        mark = "→" if j == i else ("✓" if j < i else " ")
        lines.append(f"  {mark} {j + 1}. {m.title}")
    plan_block = "\n".join(lines)
    m = milestones[i]
    return (
        f"{spec.description}\n\n"
        f"This app is being built incrementally. The full plan:\n{plan_block}\n\n"
        f"## CURRENT MILESTONE ({i + 1}/{len(milestones)}): {m.title}\n{m.goal}\n\n"
        f"Earlier milestones are already implemented in this directory — build on them, do not "
        f"redo or remove them. Implement ONLY this milestone's scope now (plus whatever is needed "
        f"to keep the app running end-to-end)."
    )
