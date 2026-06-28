"""Offline tests for the build control primitive."""

from __future__ import annotations

from harness.control import BuildControl


def test_default_none():
    assert BuildControl().requested() is None


def test_cancel():
    c = BuildControl()
    c.cancel()
    assert c.requested() == "cancelled"


def test_pause():
    c = BuildControl()
    c.pause()
    assert c.requested() == "paused"


def test_first_request_wins():
    c = BuildControl()
    c.pause()
    c.cancel()
    assert c.requested() == "paused"
