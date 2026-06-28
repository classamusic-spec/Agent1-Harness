"""Offline tests for the parallel reviewer panel."""

from __future__ import annotations

import asyncio

from harness.review import run_panel


class _Engine:
    def __init__(self, text):
        self.text = text
        self.total_tokens = 7

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, prompt, *, echo=True):
        return self.text


def test_panel_all_approve():
    texts = iter([
        '{"summary":"clean","approved":true,"findings":[]}',
        '{"summary":"no bugs","approved":true,"findings":[]}',
    ])
    verdict, tokens = asyncio.run(
        run_panel(lambda: _Engine(next(texts)), ["quality", "bugs"], echo=False)
    )
    assert verdict.approved
    assert tokens == 14  # 7 per reviewer


def test_panel_rejects_on_any_blocker():
    texts = iter([
        '{"summary":"clean","approved":true,"findings":[]}',
        '{"summary":"bug","approved":false,"findings":[{"severity":"blocker","title":"npe"}]}',
    ])
    verdict, _ = asyncio.run(
        run_panel(lambda: _Engine(next(texts)), ["quality", "bugs"], echo=False)
    )
    assert not verdict.approved
    # finding is tagged with the reviewer focus
    assert any("[bugs]" in f.title for f in verdict.findings)


def test_panel_includes_a11y_findings_tagged():
    texts = iter([
        '{"approved":false,"findings":[{"severity":"major","title":"missing alt text"}]}',
    ])
    verdict, _ = asyncio.run(
        run_panel(lambda: _Engine(next(texts)), ["a11y"], echo=False)
    )
    assert not verdict.approved
    assert verdict.findings[0].title.startswith("[a11y]")
