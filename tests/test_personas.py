"""Offline tests for persona composition."""

from __future__ import annotations

from harness.personas import BACKEND_RIGOR, FRONTEND_DESIGN, SHARED_CORE, system_prompt


def _has(prompt: str, block: str) -> bool:
    # Compare on a distinctive line so we don't depend on exact whitespace.
    marker = block.strip().splitlines()[0]
    return marker in prompt


def test_frontend_gets_design_not_backend():
    p = system_prompt("frontend")
    assert _has(p, SHARED_CORE)
    assert _has(p, FRONTEND_DESIGN)
    assert not _has(p, BACKEND_RIGOR)


def test_backend_gets_rigor_not_design():
    p = system_prompt("backend")
    assert _has(p, BACKEND_RIGOR)
    assert not _has(p, FRONTEND_DESIGN)


def test_fullstack_gets_both():
    p = system_prompt("fullstack")
    assert _has(p, FRONTEND_DESIGN)
    assert _has(p, BACKEND_RIGOR)


def test_cli_is_backend_flavoured():
    p = system_prompt("cli")
    assert _has(p, BACKEND_RIGOR)
    assert not _has(p, FRONTEND_DESIGN)


def test_unknown_kind_defaults_to_backend_inclusive():
    p = system_prompt("something-weird")
    assert _has(p, SHARED_CORE)
    assert _has(p, BACKEND_RIGOR)


def test_empty_defaults_to_fullstack():
    p = system_prompt("")
    assert _has(p, FRONTEND_DESIGN)
    assert _has(p, BACKEND_RIGOR)
