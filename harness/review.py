"""Reviewer / Sentry second gate.

After the deterministic verification suite passes, an independent reviewer agent
inspects the code and returns a structured verdict. Two focuses:

  - "quality" — a senior code reviewer (correctness, clarity, design, a11y).
  - "bugs"    — a Sentry-style bug hunter that tries to *break* the app and find
                latent defects, race conditions, and unhandled edge cases.

The verdict is parsed from the model's text. To stay robust, approval is decided
deterministically from the findings: a build is approved only if there are no
blocker/major findings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_BLOCKING = {"blocker", "major"}


@dataclass
class Finding:
    severity: str  # blocker | major | minor
    title: str
    detail: str = ""
    location: str = ""


@dataclass
class ReviewVerdict:
    approved: bool
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    raw: str = ""

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in _BLOCKING]


def _extract_json(text: str) -> dict | None:
    """Best-effort: pull the JSON verdict object out of model text."""
    if not text:
        return None
    # Strip code fences.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    # Outermost braces span.
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def extract_json(text: str) -> dict | None:
    """Public: pull the first JSON object out of model text (shared helper)."""
    return _extract_json(text)


def parse_verdict(text: str) -> ReviewVerdict:
    obj = _extract_json(text)
    if obj is None:
        return ReviewVerdict(
            approved=False,
            summary="Could not parse a review verdict from the reviewer's output.",
            findings=[Finding("major", "unparseable-review", text[:300] if text else "")],
            raw=text or "",
        )

    findings: list[Finding] = []
    for raw in obj.get("findings", []) or []:
        if not isinstance(raw, dict):
            continue
        sev = str(raw.get("severity", "minor")).lower()
        if sev not in {"blocker", "major", "minor"}:
            sev = "minor"
        findings.append(
            Finding(
                severity=sev,
                title=str(raw.get("title", "(untitled)")),
                detail=str(raw.get("detail", "")),
                location=str(raw.get("location", "")),
            )
        )

    # Deterministic gate: approve only if nothing blocking, regardless of the
    # model's self-reported boolean.
    approved = not any(f.severity in _BLOCKING for f in findings)
    return ReviewVerdict(
        approved=approved,
        summary=str(obj.get("summary", "")),
        findings=findings,
        raw=text,
    )


REVIEW_INSTRUCTION = """\
You are an independent {role} reviewing a freshly built application. The
automated build/test suite already passed — your job is to catch what tests miss.

Inspect the project files in the working directory (read the important ones).
{focus_block}

Respond with ONLY a JSON object, no prose around it:
{{
  "summary": "<one-paragraph assessment>",
  "approved": <true|false>,
  "findings": [
    {{"severity": "blocker|major|minor", "title": "...", "detail": "...", "location": "file:line"}}
  ]
}}

Severity guidance: "blocker" = broken/incorrect/insecure; "major" = a real
defect or significant quality/accessibility gap; "minor" = polish. Only report
real issues — an empty findings list with approved=true is the right answer for
clean code.
"""

_FOCUS = {
    "quality": (
        "senior code reviewer",
        "Focus on correctness, clear design, error handling, security basics, and "
        "(for UI) accessibility and visual quality.",
    ),
    "bugs": (
        "Sentry-style bug hunter",
        "Actively try to break it: think through edge cases, invalid input, "
        "concurrency, off-by-one errors, unhandled exceptions, resource leaks, and "
        "states the tests don't cover. Report the concrete failure scenario for each.",
    ),
}


def review_instruction(focus: str) -> str:
    role, focus_block = _FOCUS.get(focus, _FOCUS["quality"])
    return REVIEW_INSTRUCTION.format(role=role, focus_block=focus_block)


async def run_review(engine, focus: str, *, echo: bool = True) -> ReviewVerdict:
    """Drive a (separate, fresh-context) engine to produce a verdict."""
    text = await engine.send(review_instruction(focus), echo=echo)
    return parse_verdict(text)
