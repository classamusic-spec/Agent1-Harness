"""Offline tests for approval gates."""

from __future__ import annotations

import asyncio

from harness.approval import AutoApprove, CallbackApproval, Decision


def _ask(gate, kind="build", payload=None):
    return asyncio.run(gate.request(kind, payload or {}))


def test_auto_approve():
    assert _ask(AutoApprove()).approved is True


def test_callback_bool():
    assert _ask(CallbackApproval(lambda k, p: False)).approved is False


def test_callback_decision_with_message():
    gate = CallbackApproval(lambda k, p: Decision(False, "fix the header"))
    d = _ask(gate)
    assert not d.approved and d.message == "fix the header"


def test_callback_async():
    async def fn(kind, payload):
        return Decision(True)
    assert _ask(CallbackApproval(fn)).approved is True


def test_callback_receives_kind_and_payload():
    seen = {}
    gate = CallbackApproval(lambda k, p: seen.update(kind=k, payload=p) or True)
    _ask(gate, "plan", {"checks": [("tests", "pytest")]})
    assert seen["kind"] == "plan" and seen["payload"]["checks"][0][0] == "tests"
