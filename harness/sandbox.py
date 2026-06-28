"""Command execution backends ("exec sandbox").

Verification commands (and the local engine's shell tool) run through a
CommandRunner. Two backends:

  - host   : run on the host via the shell (default).
  - docker : run inside a throwaway container with the workspace bind-mounted,
             so the agent's shell can't touch the host filesystem.

This is orthogonal to workspace *isolation* (directory/worktree), which controls
*where files live*; the sandbox controls *where commands execute*.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Protocol


class CommandRunner(Protocol):
    def run(self, command: str, cwd: str | None, timeout: int) -> subprocess.CompletedProcess: ...


class HostRunner:
    """Run commands directly on the host."""

    def run(self, command: str, cwd: str | None, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )


def docker_argv(image: str, cwd: str | None, command: str, *, network: str | None = None) -> list[str]:
    """Build the `docker run` argv for executing `command` against `cwd`."""
    mount = os.path.abspath(cwd or os.getcwd())
    argv = ["docker", "run", "--rm", "-v", f"{mount}:/work", "-w", "/work"]
    if network is not None:
        argv += ["--network", network]
    argv += [image, "sh", "-lc", command]
    return argv


class DockerRunner:
    """Run commands inside a throwaway container with the workspace mounted."""

    def __init__(self, image: str, network: str | None = None):
        self.image = image
        self.network = network

    def run(self, command: str, cwd: str | None, timeout: int) -> subprocess.CompletedProcess:
        argv = docker_argv(self.image, cwd, command, network=self.network)
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def build_runner(config) -> CommandRunner:
    """Select the runner from a HarnessConfig (duck-typed: exec_sandbox/docker_image)."""
    mode = getattr(config, "exec_sandbox", "host")
    if mode == "docker":
        return DockerRunner(getattr(config, "docker_image", "python:3.12-slim"))
    return HostRunner()
