"""Offline tests for test-first check generation."""

from __future__ import annotations

import asyncio

from harness.spec import Spec
from harness.testfirst import propose_checks


class _Engine:
    def __init__(self, text):
        self.text = text

    async def send(self, prompt, *, echo=True):
        return self.text


def _run(text):
    spec = Spec(name="x", description="build a thing", kind="cli", language="python")
    return asyncio.run(propose_checks(_Engine(text), spec, echo=False))


def test_parses_checks():
    checks = _run('{"checks":[{"name":"tests","command":"pytest -q"},'
                  '{"name":"types","command":"mypy ."}]}')
    assert [c.name for c in checks] == ["tests", "types"]
    assert checks[0].command == "pytest -q"


def test_skips_incomplete_entries():
    checks = _run('{"checks":[{"name":"ok","command":"true"},{"name":"bad"}]}')
    assert len(checks) == 1


def test_unparseable_returns_empty():
    assert _run("here are some tests you could run") == []
