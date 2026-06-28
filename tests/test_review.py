"""Offline tests for the reviewer verdict parsing and gating."""

from __future__ import annotations

from harness.review import parse_verdict, review_instruction


def test_clean_json_approved():
    v = parse_verdict('{"summary":"ok","approved":true,"findings":[]}')
    assert v.approved and v.summary == "ok"


def test_fenced_json_with_prose():
    text = "Here is my review:\n```json\n{\"summary\":\"s\",\"approved\":true,\"findings\":[]}\n```\nThanks!"
    v = parse_verdict(text)
    assert v.approved


def test_blocker_overrides_self_reported_approval():
    text = '{"approved": true, "findings":[{"severity":"blocker","title":"crash"}]}'
    v = parse_verdict(text)
    assert not v.approved  # deterministic gate ignores the rosy boolean
    assert v.blocking and v.blocking[0].title == "crash"


def test_minor_only_is_approved():
    text = '{"approved": false, "findings":[{"severity":"minor","title":"nit"}]}'
    v = parse_verdict(text)
    assert v.approved  # only minors -> pass


def test_unparseable_is_rejected():
    v = parse_verdict("the code looks fine to me, ship it")
    assert not v.approved
    assert v.findings and v.findings[0].severity == "major"


def test_instruction_focus_variants():
    assert "bug hunter" in review_instruction("bugs")
    assert "code reviewer" in review_instruction("quality")
    assert "accessibility reviewer" in review_instruction("a11y")
    assert "security reviewer" in review_instruction("security")
    assert "injection" in review_instruction("security")
