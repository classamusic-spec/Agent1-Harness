"""Build checkpoints for resumability.

The durable state of a build is the workspace itself (the files written so far).
A checkpoint records the metadata needed to re-enter the loop against that
workspace: the spec, the config, and where the loop got to. `resume()` (in
harness.agent) loads it and continues verify -> repair -> review to green.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Checkpoint:
    spec: dict
    config: dict
    workspace: str
    status: str = "running"  # running | completed | failed | <stop_reason>
    stop_reason: str = ""
    rounds: int = 0
    escalations: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    sessions: int = 1  # how many times this build has been (re)started
    progress: list[int] = field(default_factory=list)


def save_checkpoint(path: str, cp: Checkpoint) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(cp), indent=2))


def load_checkpoint(path: str) -> Checkpoint:
    data = json.loads(Path(path).read_text())
    return Checkpoint(**data)


def checkpoint_exists(path: str) -> bool:
    return Path(path).is_file()
