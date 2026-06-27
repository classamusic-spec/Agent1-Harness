"""Offline tests for model-driven reflection."""

from __future__ import annotations

import asyncio

from harness.reflect import reflect
from harness.spec import Spec


class _Engine:
    def __init__(self, text):
        self.text = text

    async def send(self, prompt, *, echo=True):
        return self.text


def _run(engine):
    spec = Spec(name="x", description="d", kind="cli", language="python")
    return asyncio.run(reflect(engine, spec, echo=False))


def test_parses_lessons_json():
    text = '{"lessons":[{"tag":"deps","text":"pin versions"},{"tag":"io","text":"validate paths"}]}'
    lessons = _run(_Engine(text))
    assert len(lessons) == 2
    assert lessons[0].tag == "deps" and "pin versions" in lessons[0].text
    assert lessons[0].kind == "cli" and lessons[0].language == "python"


def test_caps_at_three():
    items = ",".join('{"tag":"t","text":"l%d"}' % i for i in range(6))
    lessons = _run(_Engine('{"lessons":[' + items + "]}"))
    assert len(lessons) == 3


def test_unparseable_yields_empty():
    assert _run(_Engine("no json here, looked fine")) == []


def test_empty_lessons():
    assert _run(_Engine('{"lessons":[]}')) == []
