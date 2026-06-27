"""Offline tests for the learning memory."""

from __future__ import annotations

from harness.memory import Lesson, LessonStore, lessons_from_findings, lessons_from_report
from harness.review import Finding
from harness.spec import Spec
from harness.verifier import Check, run_suite


def test_add_load_roundtrip_and_dedupe(tmp_path):
    store = LessonStore(str(tmp_path / "lessons.jsonl"))
    n1 = store.add_many([Lesson("cli", "python", "tests", "pin versions")])
    n2 = store.add_many([Lesson("cli", "python", "tests", "pin versions")])  # dup
    assert n1 == 1 and n2 == 0
    assert len(store.load()) == 1


def test_relevant_filters_and_orders(tmp_path):
    store = LessonStore(str(tmp_path / "l.jsonl"))
    store.add_many([
        Lesson("frontend", "html", "a11y", "label inputs"),
        Lesson("backend", "python", "errors", "validate input"),
    ])
    rel = store.relevant("frontend", "html")
    assert any("label inputs" in r.text for r in rel)


def test_render_empty_is_blank(tmp_path):
    store = LessonStore(str(tmp_path / "l.jsonl"))
    assert store.render("cli", "python") == ""


def test_render_has_bullets(tmp_path):
    store = LessonStore(str(tmp_path / "l.jsonl"))
    store.add_many([Lesson("cli", "python", "tests", "use a tmp dir")])
    out = store.render("cli", "python")
    assert "use a tmp dir" in out and out.strip().startswith("Lessons")


def test_lessons_from_report(tmp_path):
    report = run_suite([Check(name="build", command="echo boom 1>&2; exit 1")])
    spec = Spec(name="x", description="d", kind="cli", language="python")
    lessons = lessons_from_report(report, spec)
    assert lessons and lessons[0].tag == "build"
    assert "failed" in lessons[0].text


def test_lessons_from_findings_only_blocking():
    spec = Spec(name="x", description="d")
    findings = [
        Finding("blocker", "crash on empty", "deref None"),
        Finding("minor", "naming", "rename foo"),
    ]
    lessons = lessons_from_findings(findings, spec)
    assert len(lessons) == 1
    assert "crash on empty" in lessons[0].text
