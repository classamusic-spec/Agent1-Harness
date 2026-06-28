"""Offline tests for the exec sandbox runners."""

from __future__ import annotations

from dataclasses import dataclass

from harness.sandbox import DockerRunner, HostRunner, build_runner, docker_argv
from harness.verifier import Check, run_check


def test_host_runner_executes():
    proc = HostRunner().run("echo hi", None, 10)
    assert proc.returncode == 0
    assert "hi" in proc.stdout


def test_docker_argv_shape(tmp_path):
    argv = docker_argv("python:3.12-slim", str(tmp_path), "pytest -q")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "-v" in argv and f"{tmp_path}:/work" in argv
    # image / sh -lc / command at the tail
    assert argv[-4:] == ["python:3.12-slim", "sh", "-lc", "pytest -q"]


def test_docker_argv_network():
    argv = docker_argv("img", "/w", "ls", network="none")
    assert "--network" in argv and "none" in argv


@dataclass
class _Cfg:
    exec_sandbox: str
    docker_image: str = "img"


def test_build_runner_selects_backend():
    assert isinstance(build_runner(_Cfg("host")), HostRunner)
    assert isinstance(build_runner(_Cfg("docker")), DockerRunner)


def test_run_check_uses_injected_runner():
    calls = {}

    class FakeRunner:
        def run(self, command, cwd, timeout):
            calls["command"] = command
            import subprocess
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok", stderr="")

    result = run_check(Check(name="x", command="whatever"), runner=FakeRunner())
    assert result.ok and calls["command"] == "whatever"
