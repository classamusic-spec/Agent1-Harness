"""Offline tests for the exec sandbox runners."""

from __future__ import annotations

from dataclasses import dataclass

from harness.sandbox import (
    DockerLimits,
    DockerRunner,
    HostRunner,
    build_runner,
    docker_argv,
)
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


def _pairs(argv):
    return list(zip(argv, argv[1:]))


def test_docker_argv_hardening_defaults_on():
    # The bare argv (default limits) drops caps + blocks privilege escalation.
    argv = docker_argv("img", "/w", "ls")
    assert ("--cap-drop", "ALL") in _pairs(argv)
    assert ("--security-opt", "no-new-privileges") in _pairs(argv)


def test_docker_argv_full_limits():
    lim = DockerLimits(network="none", cpus="1.5", memory="512m", pids_limit=128,
                       read_only=True, user="1000:1000")
    argv = docker_argv("img", "/w", "ls", limits=lim)
    p = _pairs(argv)
    assert ("--network", "none") in p
    assert ("--cpus", "1.5") in p
    assert ("--memory", "512m") in p
    assert ("--pids-limit", "128") in p
    assert ("--user", "1000:1000") in p
    assert "--read-only" in argv
    assert ("--tmpfs", "/tmp") in p
    # command still last
    assert argv[-4:] == ["img", "sh", "-lc", "ls"]


def test_docker_argv_caps_can_be_relaxed():
    lim = DockerLimits(cap_drop_all=False, no_new_privileges=False)
    argv = docker_argv("img", "/w", "ls", limits=lim)
    assert "--cap-drop" not in argv and "no-new-privileges" not in argv


@dataclass
class _Cfg:
    exec_sandbox: str
    docker_image: str = "img"


def test_build_runner_selects_backend():
    assert isinstance(build_runner(_Cfg("host")), HostRunner)
    assert isinstance(build_runner(_Cfg("docker")), DockerRunner)


@dataclass
class _HardCfg:
    exec_sandbox: str = "docker"
    docker_image: str = "img"
    docker_network: str = "none"
    docker_cpus: str = "2"
    docker_memory: str = "256m"
    docker_pids_limit: int = 64
    docker_cap_drop_all: bool = True
    docker_no_new_privileges: bool = True
    docker_read_only: bool = True
    docker_user: str = "1000:1000"


def test_build_runner_applies_limits_from_config():
    runner = build_runner(_HardCfg())
    assert isinstance(runner, DockerRunner)
    lim = runner.limits
    assert lim.network == "none" and lim.cpus == "2" and lim.memory == "256m"
    assert lim.pids_limit == 64 and lim.read_only and lim.user == "1000:1000"
    argv = docker_argv("img", "/w", "pytest", limits=lim)
    assert "--read-only" in argv and ("--memory", "256m") in _pairs(argv)


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
