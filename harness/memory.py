"""Learning memory: the harness records lessons from its own mistakes and feeds
the relevant ones back into future builds.

A "lesson" is a short, reusable note tagged by project kind and language. After
a build, failures (and any reviewer findings) are distilled into lessons and
appended to a JSONL store. Before a build, the store is queried for relevant
lessons and injected into the prompt. This is the simplest durable form of
"the agent learns from itself".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lesson:
    kind: str
    language: str
    tag: str
    text: str

    def key(self) -> tuple[str, str]:
        return (self.tag, self.text)


class LessonStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> list[Lesson]:
        if not self.path.is_file():
            return []
        out: list[Lesson] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(Lesson(kind=d["kind"], language=d["language"], tag=d["tag"], text=d["text"]))
            except (json.JSONDecodeError, KeyError):
                continue  # skip corrupt lines rather than fail a build
        return out

    def add_many(self, lessons: list[Lesson]) -> int:
        """Append lessons, skipping duplicates. Returns the number written."""
        existing = {ls.key() for ls in self.load()}
        new = [ls for ls in lessons if ls.key() not in existing and ls.text.strip()]
        if not new:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            for ls in new:
                f.write(json.dumps(asdict(ls)) + "\n")
        return len(new)

    def relevant(self, kind: str, language: str, limit: int = 8) -> list[Lesson]:
        """Lessons matching this kind/language (plus generic ones), newest first."""
        all_lessons = self.load()
        kind = (kind or "").lower()
        language = (language or "").lower()

        def matches(ls: Lesson) -> bool:
            k = ls.kind.lower()
            lang = ls.language.lower()
            kind_ok = k in ("", "any", kind) or kind in ("", "fullstack")
            lang_ok = lang in ("", "any", language) or language in ("", "unspecified")
            return kind_ok or lang_ok

        picked = [ls for ls in all_lessons if matches(ls)]
        return list(reversed(picked))[:limit]

    def render(self, kind: str, language: str, limit: int = 8) -> str:
        lessons = self.relevant(kind, language, limit)
        if not lessons:
            return ""
        bullets = "\n".join(f"- ({ls.tag}) {ls.text}" for ls in lessons)
        return (
            "Lessons from earlier builds — apply them proactively so you don't "
            "repeat past mistakes:\n" + bullets
        )


def _first_line(text: str, limit: int = 200) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:limit]


def lessons_from_report(report, spec) -> list[Lesson]:
    """Distill verification failures into lessons."""
    lessons: list[Lesson] = []
    for r in getattr(report, "failures", []):
        detail = _first_line(r.error or r.stderr or r.stdout or "")
        text = f"check '{r.name}' (`{r.command}`) failed: {detail}".strip()
        lessons.append(Lesson(kind=spec.kind, language=spec.language, tag=r.name, text=text))
    return lessons


def lessons_from_findings(findings, spec) -> list[Lesson]:
    """Distill reviewer findings (blocker/major) into lessons."""
    lessons: list[Lesson] = []
    for f in findings:
        if getattr(f, "severity", "minor") in ("blocker", "major"):
            text = f"{f.title}: {_first_line(f.detail)}".strip().rstrip(":")
            lessons.append(Lesson(kind=spec.kind, language=spec.language, tag="review", text=text))
    return lessons
