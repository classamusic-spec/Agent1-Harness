"""Build-spec loading and validation.

A spec is the input to the harness: a description of the application to build
plus the verification suite that decides when it is done. Kept LLM-free so it
can be validated and unit-tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from harness.verifier import Check


class SpecError(ValueError):
    """Raised when a spec file is missing required fields or is malformed."""


@dataclass
class Spec:
    name: str
    description: str
    language: str = "unspecified"
    # Drives which specialist persona(s) the agent adopts:
    # frontend | backend | fullstack | cli | api | library
    kind: str = "fullstack"
    constraints: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def has_verification(self) -> bool:
        return bool(self.checks)


def _require(data: dict, key: str, where: str) -> object:
    if key not in data or data[key] in (None, ""):
        raise SpecError(f"spec {where}: missing required field '{key}'")
    return data[key]


def parse_spec(data: dict, *, cwd: str | None = None) -> Spec:
    """Build a Spec from an already-parsed mapping. `cwd` is applied to checks."""
    if not isinstance(data, dict):
        raise SpecError("spec must be a mapping at the top level")

    name = str(_require(data, "name", "root"))
    description = str(_require(data, "description", "root"))

    raw_checks = data.get("verification", []) or []
    if not isinstance(raw_checks, list):
        raise SpecError("spec 'verification' must be a list of checks")

    checks: list[Check] = []
    for i, raw in enumerate(raw_checks):
        if not isinstance(raw, dict):
            raise SpecError(f"verification[{i}] must be a mapping")
        checks.append(
            Check(
                name=str(_require(raw, "name", f"verification[{i}]")),
                command=str(_require(raw, "command", f"verification[{i}]")),
                cwd=cwd,
                timeout=int(raw.get("timeout", 600)),
                allow_failure=bool(raw.get("allow_failure", False)),
            )
        )

    constraints = data.get("constraints", []) or []
    if not isinstance(constraints, list):
        raise SpecError("spec 'constraints' must be a list of strings")

    return Spec(
        name=name,
        description=description,
        language=str(data.get("language", "unspecified")),
        kind=str(data.get("kind", "fullstack")),
        constraints=[str(c) for c in constraints],
        checks=checks,
    )


def spec_to_dict(spec: Spec) -> dict:
    """Serialize a Spec back to the YAML-spec mapping form (for checkpoints)."""
    return {
        "name": spec.name,
        "description": spec.description,
        "kind": spec.kind,
        "language": spec.language,
        "constraints": list(spec.constraints),
        "verification": [
            {"name": c.name, "command": c.command, "timeout": c.timeout,
             "allow_failure": c.allow_failure}
            for c in spec.checks
        ],
    }


def load_spec(path: str | Path, *, cwd: str | None = None) -> Spec:
    """Load and validate a YAML spec from disk."""
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"spec file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SpecError(f"could not parse YAML in {path}: {exc}") from exc
    return parse_spec(data or {}, cwd=cwd)
