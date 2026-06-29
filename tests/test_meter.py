"""Tests for the live token/sec meter."""

from __future__ import annotations

from harness.meter import TokenMeter, approx_tokens


def test_approx_tokens():
    assert approx_tokens("") == 0
    assert approx_tokens("a") == 1
    assert approx_tokens("x" * 40) == 10        # ~4 chars/token


def test_rate_uses_injected_clock():
    clock = [100.0]
    m = TokenMeter(now=lambda: clock[0])
    m.start()
    m.add(300)
    clock[0] = 103.0                            # 3 seconds elapsed
    assert round(m.rate()) == 100               # 300 tok / 3s
    assert round(m.elapsed()) == 3


def test_due_throttles_to_interval():
    clock = [0.0]
    m = TokenMeter(now=lambda: clock[0], every=1.5)
    m.start()
    assert m.due() is False                     # nothing elapsed since start
    clock[0] = 1.4
    assert m.due() is False
    clock[0] = 1.6
    assert m.due() is True                      # past the interval
    assert m.due() is False                     # consumed -> throttled again


def test_set_tokens_only_raises():
    m = TokenMeter()
    m.add(10)
    m.set_tokens(50)
    assert m.tokens == 50
    m.set_tokens(20)                            # exact count lower than estimate -> ignore
    assert m.tokens == 50


def test_rate_zero_before_start():
    m = TokenMeter()
    assert m.rate() == 0.0 and m.elapsed() == 0.0


def test_line_is_human_readable():
    clock = [0.0]
    m = TokenMeter(now=lambda: clock[0])
    m.start(); m.add(1500); clock[0] = 10.0
    line = m.line()
    assert "tok/s" in line and "1,500 tok" in line
