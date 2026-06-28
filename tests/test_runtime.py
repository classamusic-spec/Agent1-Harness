"""Tests for the dev-server runtime: port allocation, command detection, and a
real Service lifecycle (start a python http.server, health-check, logs, stop)."""

from __future__ import annotations

import json
import time
import urllib.request

from harness.runtime import RuntimeManager, Service, detect_command, free_port


def test_free_port_is_usable():
    p = free_port()
    assert isinstance(p, int) and 1024 < p < 65536


def test_detect_command_node(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "build": "x"}}))
    d = detect_command(str(tmp_path))
    assert d and d["command"] == "npm run dev" and d["kind"] == "node"


def test_detect_command_python(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')")
    d = detect_command(str(tmp_path))
    assert d and d["command"] == "python main.py" and d["kind"] == "python"


def test_detect_command_static(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    d = detect_command(str(tmp_path))
    assert d and d["kind"] == "static" and "http.server" in d["command"]


def test_detect_command_none(tmp_path):
    assert detect_command(str(tmp_path)) is None


def _wait_ready(svc: Service, timeout: float = 8.0) -> None:
    end = time.time() + timeout
    while time.time() < end and svc.status not in ("ready", "running", "failed"):
        time.sleep(0.2)


def test_service_lifecycle_serves_and_stops(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><h1>runtime ok</h1>")
    rt = RuntimeManager()
    svc = rt.start(str(tmp_path), "python -m http.server $PORT")
    try:
        _wait_ready(svc)
        assert svc.status == "ready"
        body = urllib.request.urlopen(f"http://127.0.0.1:{svc.port}/", timeout=3).read().decode()
        assert "runtime ok" in body
        lines, nxt = svc.logs()
        assert nxt >= 1 and any("http.server" in ln for ln in lines)
        assert rt.for_workspace(tmp_path.name) is svc
    finally:
        rt.stop()
    time.sleep(0.4)
    assert svc.status == "stopped"


def test_manager_keeps_one_server(tmp_path):
    (tmp_path / "index.html").write_text("<h1>a</h1>")
    rt = RuntimeManager()
    a = rt.start(str(tmp_path), "python -m http.server $PORT")
    _wait_ready(a)
    b = rt.start(str(tmp_path), "python -m http.server $PORT")  # replaces a
    _wait_ready(b)
    try:
        assert rt.current() is b
        time.sleep(0.3)
        assert a.status == "stopped"  # previous one was stopped
    finally:
        rt.stop()
