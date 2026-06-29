"""Project memory / design system — make every app feel like *yours*.

A small JSON-backed profile of the user's taste (preferred stack, palette,
typography, UI style, component conventions, tone). It's rendered into a prompt
block injected at build/iterate time so the agent matches your style by default,
and it can **learn** from what you ship — extracting the palette from a build's CSS
and remembering it, so the look converges over time.

Stdlib only; the profile is just a dict persisted to JSON.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter

FIELDS = ("stack", "ui_style", "palette", "typography", "components", "tone", "notes")


def empty_profile() -> dict:
    return {"stack": "", "ui_style": "", "palette": [], "typography": "",
            "components": "", "tone": "", "notes": "", "auto_learn": True}


def load(path: str) -> dict:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_profile()
    prof = empty_profile()
    if isinstance(data, dict):
        prof.update({k: v for k, v in data.items() if k in prof})
    if not isinstance(prof.get("palette"), list):
        prof["palette"] = [s.strip() for s in str(prof["palette"]).split(",") if s.strip()]
    return prof


def save(path: str, profile: dict) -> dict:
    prof = empty_profile()
    prof.update({k: v for k, v in (profile or {}).items() if k in prof})
    if isinstance(prof["palette"], str):
        prof["palette"] = [s.strip() for s in prof["palette"].split(",") if s.strip()]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(prof, fh, indent=2)
    return prof


def is_empty(profile: dict | None) -> bool:
    if not profile:
        return True
    return not any(profile.get(f) for f in FIELDS)


def render(profile: dict | None) -> str:
    """Render the profile as a prompt block (empty string if nothing set)."""
    if is_empty(profile):
        return ""
    p = profile
    lines = ["The user has a house style — honor it unless the spec explicitly overrides:"]
    if p.get("stack"):
        lines.append(f"- Preferred stack: {p['stack']}")
    if p.get("ui_style"):
        lines.append(f"- UI style: {p['ui_style']}")
    pal = p.get("palette") or []
    if pal:
        lines.append(f"- Palette (use these colors): {', '.join(pal)}")
    if p.get("typography"):
        lines.append(f"- Typography: {p['typography']}")
    if p.get("components"):
        lines.append(f"- Component conventions: {p['components']}")
    if p.get("tone"):
        lines.append(f"- Voice/tone: {p['tone']}")
    if p.get("notes"):
        lines.append(f"- Also: {p['notes']}")
    return "\n".join(lines)


_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".studio", "dist", "build", ".venv"}
# Near-black / near-white are usually structural, not brand colors.
_GENERIC = {"#000", "#000000", "#fff", "#ffffff"}


def extract_palette(workspace: str, top: int = 6) -> list[str]:
    """The most-used brand-ish hex colors across a workspace's CSS/HTML/JS."""
    counts: Counter = Counter()
    for dp, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith((".css", ".html", ".js", ".jsx", ".ts", ".tsx", ".svg")):
                continue
            try:
                text = open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in _HEX_RE.findall(text):
                c = m.lower()
                if c not in _GENERIC:
                    counts[c] += 1
    return [c for c, _ in counts.most_common(top)]


def learn_from_workspace(profile: dict, workspace: str, *, max_palette: int = 6) -> dict:
    """Merge a build's palette into the profile (most-used colors win, deduped)."""
    found = extract_palette(workspace, top=max_palette)
    if not found:
        return profile
    existing = list(profile.get("palette") or [])
    merged = existing + [c for c in found if c not in existing]
    profile["palette"] = merged[:max_palette]
    return profile
