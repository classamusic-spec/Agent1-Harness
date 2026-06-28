"""Multi-agent decomposition — role specialists on isolated worktrees.

Instead of one generalist agent building an entire full-stack app, split the work
across role specialists (a **backend** agent and a **frontend** agent) that build
in parallel on isolated copies of the workspace, then **merge by file ownership**
and drive an **integration** agent to green on the full app suite.

So the parts fit together when merged, an **architect** first writes a shared
*contract* — the API surface (routes + request/response shapes), the data model,
and which files each role owns. Each role builds against that contract and only its
owned paths are taken at merge time, so disjoint ownership makes the merge
conflict-free. Each role runs the normal `agent.build` loop (its own verify/repair),
so all the existing machinery — sandbox, personas, telemetry — applies.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import time
from dataclasses import dataclass, field

from harness.config import HarnessConfig
from harness.spec import Spec

CONTRACT_SYSTEM = (
    "You are a senior software architect. You define a tight, unambiguous contract "
    "that lets a backend specialist and a frontend specialist build their parts "
    "independently and have them fit together. You do NOT write application code."
)

# Directories never worth copying into a role's isolated worktree.
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "node_modules", "__pycache__", ".studio", ".pytest_cache",
    ".venv", "venv", "dist", "build", ".next", ".cache", "*.db", "*.db-wal", "*.db-shm")


@dataclass
class Role:
    name: str            # "backend" | "frontend"
    kind: str            # persona kind for make_engine (drives the system prompt)
    goal: str            # the role's responsibility / scope
    owns: list[str]      # path prefixes this role owns (merged back; others discarded)
    checks: list[dict] = field(default_factory=list)  # role-local verification


def default_roles(spec: Spec) -> list[Role]:
    """The role split for a spec. Full-stack → backend + frontend; otherwise one role."""
    k = (spec.kind or "fullstack").strip().lower()
    backend = Role(
        name="backend", kind="backend",
        goal=("Build the server, data layer, and JSON API exactly as the contract "
              "specifies. Own all server-side code; do not build the UI."),
        owns=["server.py", "app.py", "main.py", "wsgi.py", "manage.py", "migrate.py",
              "migrations", "seed.sql", "api", "models", "db", "requirements.txt", ".env.example"],
        checks=[
            {"name": "compiles", "command": "python -m compileall -q . || true"},
            {"name": "api health", "command": (
                "python3 -c \"import urllib.request,sys; "
                "sys.exit(0 if urllib.request.urlopen('$APP_URL/api/health', timeout=6)"
                ".status==200 else 1)\""), "needs_server": True},
        ])
    frontend = Role(
        name="frontend", kind="frontend",
        goal=("Build the user interface exactly as the contract specifies, calling the "
              "backend's JSON API (relative /api/... URLs). Own the frontend files only; "
              "do not modify server-side code."),
        owns=["public", "src", "index.html", "styles.css", "app.js", "static", "assets"],
        checks=[
            {"name": "frontend present", "command": (
                "python3 -c \"import glob,sys; "
                "sys.exit(0 if glob.glob('**/index.html', recursive=True) else 1)\"")},
        ])
    if k in ("frontend", "ui", "web", "react", "react-native", "mobile", "static"):
        return [frontend]
    if k in ("backend", "api", "cli", "server"):
        return [backend]
    return [backend, frontend]


def contract_prompt(spec: Spec, roles: list[Role], scaffold_note: str = "") -> str:
    base = (f"\nStarting base (the contract must fit this stack & run command):\n{scaffold_note}\n"
            if scaffold_note else "")
    role_list = "\n".join(f"  - {r.name}: {r.goal}" for r in roles)
    return (
        f"Define the build contract for this application.\n\n"
        f"App: **{spec.name}** (kind: {spec.kind}, stack: {spec.language})\n"
        f"Specification:\n{spec.description}\n"
        f"{base}\n"
        f"Roles that will build independently against your contract:\n{role_list}\n\n"
        f"Write a concise contract (plain text/markdown, no application code) covering:\n"
        f"1. **API** — every endpoint: method, path (under /api), request body, response "
        f"shape + status codes. Include GET /api/health -> 200 {{\"ok\": true}}.\n"
        f"2. **Data model** — tables/fields (and migration file names if relevant).\n"
        f"3. **File layout** — which files the backend owns vs. the frontend, and how the "
        f"frontend reaches the API (relative /api URLs).\n"
        f"Keep it short and unambiguous — both specialists will follow it verbatim."
    )


async def make_contract(spec: Spec, config: HarnessConfig, roles: list[Role], *,
                        echo: bool = True, scaffold_note: str = "") -> str:
    """Ask an architect engine for the shared contract. Falls back to a stub."""
    from harness.engines.base import make_engine
    engine = make_engine(spec, config, system_prompt_override=CONTRACT_SYSTEM)
    try:
        async with engine:
            return await engine.send(contract_prompt(spec, roles, scaffold_note), echo=False)
    except Exception as exc:
        if echo:
            print(f"[multi] contract step failed ({type(exc).__name__}: {exc}); "
                  "roles will build from the spec alone.", flush=True)
        return ""


def role_description(spec: Spec, role: Role, contract: str, roles: list[Role]) -> str:
    others = [r for r in roles if r.name != role.name]
    owned = ", ".join(role.owns)
    foreign = ", ".join(p for r in others for p in r.owns) or "(none)"
    contract_block = f"\n## SHARED CONTRACT (follow verbatim)\n{contract}\n" if contract else ""
    coworkers = (f"Another agent is building: {', '.join(r.name for r in others)}. "
                 if others else "")
    return (
        f"{spec.description}\n"
        f"{contract_block}\n"
        f"## YOUR ROLE: {role.name}\n{role.goal}\n\n"
        f"{coworkers}Implement ONLY your part. You OWN these paths (only your changes to "
        f"them are kept): {owned}. Do NOT modify paths owned by other roles ({foreign}) — "
        f"treat any that exist as a read-only contract reference. Keep the app runnable."
    )


def role_spec(spec: Spec, role: Role, contract: str, roles: list[Role], ws: str) -> Spec:
    from harness.verifier import Check
    checks = [Check(name=c["name"], command=c["command"], cwd=ws,
                    needs_server=bool(c.get("needs_server")),
                    allow_failure=bool(c.get("allow_failure"))) for c in role.checks]
    return Spec(
        name=f"{spec.name} [{role.name}]",
        description=role_description(spec, role, contract, roles),
        language=spec.language, kind=role.kind, constraints=list(spec.constraints),
        checks=checks, run=spec.run, scaffold=None)


def _copy_ws(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=_COPY_IGNORE, dirs_exist_ok=True)


def merge_owned(base_ws: str, role_ws: str, owns: list[str]) -> list[str]:
    """Copy a role's owned paths from its worktree back into the base. Returns the
    paths merged. Files/dirs not owned by the role are ignored (discarded)."""
    merged: list[str] = []
    for rel in owns:
        src = os.path.join(role_ws, rel)
        dst = os.path.join(base_ws, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            merged.append(rel + "/")
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst) or base_ws, exist_ok=True)
            shutil.copy2(src, dst)
            merged.append(rel)
    return merged


def integration_description(spec: Spec, contract: str, roles: list[Role]) -> str:
    contract_block = f"\n## SHARED CONTRACT\n{contract}\n" if contract else ""
    names = " + ".join(r.name for r in roles)
    return (
        f"{spec.description}\n{contract_block}\n"
        f"## INTEGRATION\nThe {names} parts were built separately and merged into this "
        f"directory. Make the whole app work end-to-end: fix any integration gaps so the "
        f"frontend talks to the backend per the contract, the server boots, and the full "
        f"verification suite passes. Prefer small, targeted fixes over rewrites."
    )


async def build_multi(spec, config, *, echo=True, builder_factory=None, reviewer_factory=None,
                      fixer_factory=None, planner_factory=None, approval=None,
                      on_progress=None, control=None) -> "object":
    """Decompose into role builds (parallel, isolated worktrees), merge by ownership,
    then drive an integration agent to green on the full suite."""
    from harness import agent, stacks
    from harness.agent import BuildResult
    bf = builder_factory or agent._default_builder
    rf = reviewer_factory or agent._default_reviewer
    xf = fixer_factory or agent._default_fixer
    pf = planner_factory or agent._default_planner

    ws = config.workspace
    os.makedirs(ws, exist_ok=True)
    start = time.monotonic()

    # Scaffold the shared base once (roles inherit it via their worktree copies).
    scaffold_note = ""
    scaffold_name = config.scaffold or spec.scaffold
    if scaffold_name:
        from harness import scaffolds
        sc = scaffolds.apply(scaffold_name, ws)
        if sc:
            scaffold_note = sc.note
            if not spec.run:
                spec.run = sc.run

    roles = default_roles(spec)
    if echo:
        print(_banner(f"multi-agent: {', '.join(r.name for r in roles)} "
                      f"({'parallel' if config.multi_parallel else 'sequential'})"), flush=True)

    contract = await make_contract(spec, config, roles, echo=echo, scaffold_note=scaffold_note)
    if echo and contract:
        print(_banner("shared contract"), flush=True)
        print(contract.strip()[:1200], flush=True)

    committed = 0
    outcomes: list[dict] = []

    def relay(p):
        if on_progress:
            on_progress({"tokens": committed + p.get("tokens", 0),
                         "elapsed": round(time.monotonic() - start, 2)})

    async def run_role(role: Role):
        role_ws = ws
        if config.multi_parallel and len(roles) > 1:
            parent = os.path.dirname(os.path.abspath(ws.rstrip("/"))) or "."
            role_ws = os.path.join(parent, f"{os.path.basename(ws.rstrip('/'))}.role-{role.name}")
            _copy_ws(ws, role_ws)
        sub_spec = role_spec(spec, role, contract, roles, role_ws)
        sub_config = dataclasses.replace(
            config, workspace=role_ws, multi=False, plan=False, scaffold=None,
            test_first=False, approve_plan=False, approve_build=False,
            checkpoint_path=None, enable_review=False, review_panel=[])
        if echo:
            print(_banner(f"role: {role.name}"), flush=True)
        res = await agent.build(sub_spec, sub_config, echo=echo, builder_factory=bf,
                                reviewer_factory=rf, fixer_factory=xf, planner_factory=pf,
                                approval=None, on_progress=relay, control=control)
        return role, role_ws, res

    if config.multi_parallel and len(roles) > 1:
        results = await asyncio.gather(*(run_role(r) for r in roles))
    else:
        results = [await run_role(r) for r in roles]

    for role, role_ws, res in results:
        committed += res.tokens_used
        merged = []
        if role_ws != ws:
            merged = merge_owned(ws, role_ws, role.owns)
            shutil.rmtree(role_ws, ignore_errors=True)
        outcomes.append({"title": role.name, "ok": res.ok, "tokens": res.tokens_used,
                         "stop_reason": res.stop_reason, "merged": merged})
        if echo:
            print(f"  role {role.name}: {'PASSED' if res.ok else 'FAILED'} "
                  f"({res.stop_reason}, {res.tokens_used} tokens)"
                  + (f"; merged {', '.join(merged)}" if merged else ""), flush=True)

    # If any role failed, the merged app can't integrate — stop before the gate.
    if not all(o["ok"] for o in outcomes):
        failed = next(o["title"] for o in outcomes if not o["ok"])
        if echo:
            print(_banner(f"role '{failed}' failed — skipping integration"), flush=True)
        return BuildResult(
            ok=False, rounds=len(outcomes), stop_reason=f"role-failed: {failed}",
            workspace=ws, tokens_used=committed,
            elapsed_seconds=round(time.monotonic() - start, 2), roles=outcomes)

    # Integration: drive the merged app to green on the full suite.
    base_gate = list(spec.checks) if spec.checks else stacks.default_checks(ws, spec.kind)
    for c in base_gate:
        c.cwd = ws
    integ_spec = Spec(
        name=f"{spec.name} [integration]", description=integration_description(spec, contract, roles),
        language=spec.language, kind=spec.kind, constraints=list(spec.constraints),
        checks=base_gate, run=spec.run, scaffold=None)
    integ_config = dataclasses.replace(
        config, workspace=ws, multi=False, plan=False, scaffold=None, test_first=False,
        approve_plan=False, approve_build=False, checkpoint_path=None)
    if echo:
        print(_banner("integration: full-suite gate on the merged app"), flush=True)
    integ = await agent.build(integ_spec, integ_config, echo=echo, builder_factory=bf,
                              reviewer_factory=rf, fixer_factory=xf, planner_factory=pf,
                              approval=approval, on_progress=relay, control=control)
    committed += integ.tokens_used
    outcomes.append({"title": "integration", "ok": integ.ok, "tokens": integ.tokens_used,
                     "stop_reason": integ.stop_reason})

    ok = integ.ok
    reason = "verified" if ok else f"integration-{integ.stop_reason}"
    return BuildResult(
        ok=ok, rounds=len(outcomes), stop_reason=reason, report=integ.report,
        verdict=integ.verdict, workspace=ws, tokens_used=committed,
        elapsed_seconds=round(time.monotonic() - start, 2), roles=outcomes)


def _banner(text: str) -> str:
    from harness.agent import _banner as b
    return b(text)
