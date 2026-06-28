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

import dataclasses
import os
import shutil
import subprocess
from typing import Protocol


class CommandRunner(Protocol):
    def run(self, command: str, cwd: str | None, timeout: int,
            env: dict | None = None) -> subprocess.CompletedProcess: ...


class HostRunner:
    """Run commands directly on the host."""

    def run(self, command: str, cwd: str | None, timeout: int,
            env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = None
        if env:
            full_env = dict(os.environ)
            full_env.update(env)
        return subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=full_env,
        )


@dataclasses.dataclass
class DockerLimits:
    """Hardening / resource policy for the Docker exec sandbox."""
    network: str | None = None         # None = default bridge; "none" isolates the build
    cpus: str | None = None            # e.g. "1.5"
    memory: str | None = None          # e.g. "512m"
    pids_limit: int | None = None      # cap process count
    cap_drop_all: bool = True          # drop all Linux capabilities
    no_new_privileges: bool = True     # block privilege escalation
    read_only: bool = False            # read-only rootfs (+ tmpfs for scratch)
    user: str | None = None            # run as a non-root uid:gid, e.g. "1000:1000"
    tmpfs: tuple = ("/tmp",)           # writable tmpfs mounts when read_only


def docker_argv(image: str, cwd: str | None, command: str, *, network: str | None = None,
                env: dict | None = None, limits: "DockerLimits | None" = None) -> list[str]:
    """Build the `docker run` argv for executing `command` against `cwd`."""
    lim = limits or DockerLimits(network=network)
    if network is not None and limits is None:
        lim.network = network
    mount = os.path.abspath(cwd or os.getcwd())
    argv = ["docker", "run", "--rm", "-v", f"{mount}:/work", "-w", "/work"]
    if lim.network is not None:
        argv += ["--network", lim.network]
    if lim.cpus:
        argv += ["--cpus", str(lim.cpus)]
    if lim.memory:
        argv += ["--memory", str(lim.memory)]
    if lim.pids_limit:
        argv += ["--pids-limit", str(lim.pids_limit)]
    if lim.cap_drop_all:
        argv += ["--cap-drop", "ALL"]
    if lim.no_new_privileges:
        argv += ["--security-opt", "no-new-privileges"]
    if lim.user:
        argv += ["--user", lim.user]
    if lim.read_only:
        argv += ["--read-only"]
        for t in lim.tmpfs:
            argv += ["--tmpfs", t]
    for k, v in (env or {}).items():
        argv += ["-e", f"{k}={v}"]
    argv += [image, "sh", "-lc", command]
    return argv


class DockerRunner:
    """Run commands inside a throwaway container with the workspace mounted."""

    def __init__(self, image: str, network: str | None = None,
                 limits: "DockerLimits | None" = None):
        self.image = image
        self.limits = limits or DockerLimits(network=network)

    def run(self, command: str, cwd: str | None, timeout: int,
            env: dict | None = None) -> subprocess.CompletedProcess:
        argv = docker_argv(self.image, cwd, command, env=env, limits=self.limits)
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def build_runner(config) -> CommandRunner:
    """Select the runner from a HarnessConfig (duck-typed: exec_sandbox/docker_image)."""
    mode = getattr(config, "exec_sandbox", "host")
    if mode == "docker":
        limits = DockerLimits(
            network=getattr(config, "docker_network", None),
            cpus=getattr(config, "docker_cpus", None),
            memory=getattr(config, "docker_memory", None),
            pids_limit=getattr(config, "docker_pids_limit", None),
            cap_drop_all=getattr(config, "docker_cap_drop_all", True),
            no_new_privileges=getattr(config, "docker_no_new_privileges", True),
            read_only=getattr(config, "docker_read_only", False),
            user=getattr(config, "docker_user", None),
        )
        return DockerRunner(getattr(config, "docker_image", "python:3.12-slim"), limits=limits)
    return HostRunner()
