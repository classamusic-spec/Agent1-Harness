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
        def run(self, command, cwd, timeout, env=None):
            calls["command"] = command
            calls["env"] = env
            import subprocess
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok", stderr="")

    result = run_check(Check(name="x", command="whatever"), runner=FakeRunner(),
                       env={"DATABASE_URL": "app.db"})
    assert result.ok and calls["command"] == "whatever"
    assert calls["env"] == {"DATABASE_URL": "app.db"}


def test_host_runner_injects_env():
    proc = HostRunner().run("echo $MY_VAR", None, 10, env={"MY_VAR": "hello-env"})
    assert proc.returncode == 0
    assert "hello-env" in proc.stdout


def test_host_runner_env_layers_over_os_environ():
    # env extends os.environ rather than replacing it (PATH still resolves echo).
    proc = HostRunner().run("echo ok", None, 10, env={"EXTRA": "1"})
    assert proc.returncode == 0 and "ok" in proc.stdout


def test_docker_argv_passes_env_flags():
    argv = docker_argv("img", "/w", "ls", env={"DATABASE_URL": "app.db"})
    assert "-e" in argv and "DATABASE_URL=app.db" in argv
