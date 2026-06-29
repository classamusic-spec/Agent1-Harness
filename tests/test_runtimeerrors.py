"""Tests for runtime-error extraction from dev-server logs."""

from __future__ import annotations

from harness import runtimeerrors


PY_LOG = """\
$ python app.py
[ready] http://127.0.0.1:5000/
127.0.0.1 - - "GET / HTTP/1.1" 200
Traceback (most recent call last):
  File "app.py", line 42, in handle
    return db.query(sql)
  File "db.py", line 10, in query
    raise ValueError("no such column: titel")
ValueError: no such column: titel
127.0.0.1 - - "GET / HTTP/1.1" 500
"""


def test_extracts_full_python_traceback():
    err = runtimeerrors.extract(PY_LOG)
    assert err.startswith("Traceback (most recent call last):")
    assert err.rstrip().endswith("ValueError: no such column: titel")
    assert "GET / HTTP" not in err          # request noise excluded


def test_extracts_node_stack():
    log = ("Server listening on 3000\n"
           "ReferenceError: foo is not defined\n"
           "    at handler (/app/index.js:12:5)\n"
           "    at Server.emit (node:events:514:28)\n"
           "GET /api 500\n")
    err = runtimeerrors.extract(log)
    assert "ReferenceError: foo is not defined" in err
    assert "/app/index.js:12:5" in err       # stack frame pulled in


def test_extracts_missing_module():
    log = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'flask'\n"
    err = runtimeerrors.extract(log)
    assert "ModuleNotFoundError" in err


def test_extract_returns_empty_when_no_error():
    assert runtimeerrors.extract("GET / 200\nGET /style.css 200\n") == ""
    assert runtimeerrors.extract("") == ""


def test_from_service_logs_falls_back_to_tail():
    lines = [f"line {i}" for i in range(40)]
    out = runtimeerrors.from_service_logs(lines, tail=5)
    assert out.splitlines() == ["line 35", "line 36", "line 37", "line 38", "line 39"]


def test_from_service_logs_prefers_error_over_tail():
    lines = ["GET / 200"] * 30 + ["TypeError: x is not a function", "    at f (a.js:1:1)"]
    out = runtimeerrors.from_service_logs(lines)
    # leads with the error + its stack frame (a couple context lines are allowed)
    assert "TypeError: x is not a function" in out and "at f (a.js:1:1)" in out
    assert out.count("GET / 200") <= 2          # not the whole 30-line tail


def test_has_error():
    assert runtimeerrors.has_error(["all good", "SyntaxError: bad token"]) is True
    assert runtimeerrors.has_error(["GET / 200", "served ok"]) is False
