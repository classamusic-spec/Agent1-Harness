"""Test-first (red -> green): derive the verification suite from the spec before
building, then hold the build to it.

A planner agent reads the spec and proposes concrete verification commands
(build / typecheck / lint / tests). Those become the gate, so the first build
round runs "red" and the loop drives it to "green". Robust: any parse failure
falls back to whatever checks the spec already declared.
"""

from __future__ import annotations

from harness.review import extract_json
from harness.spec import Spec
from harness.verifier import Check

PLAN_PROMPT = """\
You are defining the acceptance tests for an app BEFORE it is written.

Spec name: {name}
Kind: {kind}
Language: {language}

Description:
{description}

Propose a small, concrete verification suite — shell commands that must exit 0
for the app to be considered done. Prefer real tooling for the language (build,
typecheck, lint, unit tests). Each command runs from the project root. Keep it
to 2-5 checks. Commands must be runnable as written.

Respond with ONLY JSON, no prose:
{{"checks": [{{"name": "tests", "command": "pytest -q"}}]}}
"""

_MAX_CHECKS = 6


async def propose_checks(engine, spec: Spec, *, echo: bool = True) -> list[Check]:
    """Ask the engine for a verification suite. Returns [] on any problem."""
    prompt = PLAN_PROMPT.format(
        name=spec.name, kind=spec.kind, language=spec.language, description=spec.description
    )
    try:
        text = await engine.send(prompt, echo=echo)
    except Exception:
        return []

    obj = extract_json(text)
    if not isinstance(obj, dict):
        return []

    out: list[Check] = []
    for raw in (obj.get("checks") or [])[:_MAX_CHECKS]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        command = str(raw.get("command", "")).strip()
        if name and command:
            out.append(Check(name=name, command=command))
    return out
