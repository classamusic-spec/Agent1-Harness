"""Offline tests for spec loading/validation."""

from __future__ import annotations

import textwrap

import pytest

from harness.spec import SpecError, load_spec, parse_spec


def test_parse_minimal_spec():
    spec = parse_spec({"name": "x", "description": "do a thing"})
    assert spec.name == "x"
    assert spec.description == "do a thing"
    assert spec.language == "unspecified"
    assert spec.checks == []
    assert spec.has_verification is False


def test_parse_full_spec_applies_cwd_to_checks():
    spec = parse_spec(
        {
            "name": "app",
            "description": "desc",
            "language": "python",
            "constraints": ["stdlib only"],
            "verification": [
                {"name": "tests", "command": "pytest -q", "timeout": 30},
            ],
        },
        cwd="/tmp/ws",
    )
    assert spec.language == "python"
    assert spec.constraints == ["stdlib only"]
    assert len(spec.checks) == 1
    assert spec.checks[0].cwd == "/tmp/ws"
    assert spec.checks[0].timeout == 30
    assert spec.has_verification is True


def test_missing_required_field_raises():
    with pytest.raises(SpecError):
        parse_spec({"name": "x"})  # no description


def test_check_missing_command_raises():
    with pytest.raises(SpecError):
        parse_spec(
            {"name": "x", "description": "d", "verification": [{"name": "tests"}]}
        )


def test_load_spec_from_file(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        textwrap.dedent(
            """
            name: demo
            description: build a demo
            verification:
              - name: compile
                command: "true"
            """
        )
    )
    spec = load_spec(p, cwd=str(tmp_path))
    assert spec.name == "demo"
    assert spec.checks[0].command == "true"


def test_load_missing_file_raises():
    with pytest.raises(SpecError):
        load_spec("/nonexistent/spec.yaml")
