"""Model-driven reflection: the agent distills reusable lessons from its build.

This is a richer complement to the mechanical lesson extraction in
`harness.memory`. After a build (especially one that needed repairs or was
rejected), the builder engine — which still holds the full session context — is
asked to summarize a few *generalizable* lessons. Robust by design: any parse
failure yields no reflected lessons and the caller falls back to the mechanical
distillation.
"""

from __future__ import annotations

from harness.memory import Lesson
from harness.review import extract_json
from harness.spec import Spec

REFLECT_PROMPT = """\
The build is complete. Reflect on the mistakes made during THIS build and distill
at most 3 short, REUSABLE lessons that would help you avoid the same mistakes on
a future, unrelated project. Skip anything specific to this project's domain.

Respond with ONLY JSON, no prose:
{"lessons": [{"tag": "short-kebab-tag", "text": "the lesson, one sentence"}]}

If there were no meaningful mistakes, return {"lessons": []}.
"""

_MAX_LESSONS = 3
_MAX_TEXT = 240


async def reflect(engine, spec: Spec, *, echo: bool = True) -> list[Lesson]:
    """Ask the engine for reusable lessons. Returns [] on any problem."""
    try:
        text = await engine.send(REFLECT_PROMPT, echo=echo)
    except Exception:
        return []

    obj = extract_json(text)
    if not isinstance(obj, dict):
        return []

    out: list[Lesson] = []
    for raw in (obj.get("lessons") or [])[:_MAX_LESSONS]:
        if not isinstance(raw, dict):
            continue
        body = str(raw.get("text", "")).strip()[:_MAX_TEXT]
        if not body:
            continue
        tag = str(raw.get("tag", "reflection")).strip()[:40] or "reflection"
        out.append(Lesson(kind=spec.kind, language=spec.language, tag=tag, text=body))
    return out
