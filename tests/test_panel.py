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


def test_review_instruction_includes_patch_block():
    from harness.review import review_instruction
    plain = review_instruction("quality")
    assert "```diff" not in plain
    patched = review_instruction("quality", diff="--- a\n+++ b\n+danger()")
    assert "ITERATIVE build" in patched
    assert "```diff" in patched and "+danger()" in patched
    # an all-whitespace diff is treated as no diff
    assert "```diff" not in review_instruction("quality", diff="   \n  ")


def test_panel_forwards_diff_to_reviewers():
    seen = []

    class _CapEngine(_Engine):
        async def send(self, prompt, *, echo=True):
            seen.append(prompt)
            return self.text

    asyncio.run(run_panel(
        lambda: _CapEngine('{"summary":"ok","approved":true,"findings":[]}'),
        ["quality"], echo=False, diff="--- a\n+++ b\n-old\n+new"))
    assert any("```diff" in p and "+new" in p for p in seen)
