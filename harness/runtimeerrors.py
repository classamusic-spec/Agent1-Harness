"""Extract the real error out of dev-server logs.

A failing server check hands the model the *tail* of the dev-server log — but the
actual cause (a Python traceback, a Node stack, a missing module, a port clash) is
often buried under request noise, or scrolled off the 15-line tail. This pulls the
signal out: it finds the most relevant error block (a full traceback, the first
stack, or a single error line with context) and returns it so the repair prompt
leads with the cause instead of noise. It also recognises boot-time failures even
when an HTTP check nominally "passed" with a 500.

Stdlib only; pure + unit-tested.
"""

from __future__ import annotations

import re

# Single-line error signatures across common stacks, most specific first.
_ERROR_LINE = re.compile(
    r"(Traceback \(most recent call last\)|"
    r"\w*Error: |\w*Exception: |ModuleNotFoundError|ImportError|SyntaxError|"
    r"ReferenceError|TypeError:|UnhandledPromiseRejection|"
    r"Cannot find module|EADDRINUSE|ECONNREFUSED|panic:|"
    r"FATAL|Unhandled exception|Segmentation fault)",
    re.IGNORECASE)

# Lines that look like a Python traceback frame or a JS stack frame.
_FRAME = re.compile(r"^\s+(File \"|at \w|at /)")


def _python_traceback(lines: list[str], start: int) -> tuple[int, int]:
    """Bounds of a Python traceback starting at `start` (header line) through the
    first non-indented exception line that closes it."""
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        # The terminating exception line is non-indented and looks like 'XError: ...'.
        if ln and not ln[:1].isspace() and re.match(r"[\w.]+(Error|Exception|Warning)\b", ln):
            return start, i + 1
        i += 1
    return start, min(len(lines), start + 30)


def extract(log_text: str, *, context: int = 2, max_lines: int = 40) -> str:
    """Return the most relevant error block from a log, or '' if none is found."""
    if not log_text:
        return ""
    lines = log_text.splitlines()
    for i, ln in enumerate(lines):
        if "Traceback (most recent call last)" in ln:
            a, b = _python_traceback(lines, i)
            return "\n".join(lines[a:b][:max_lines]).strip()
    for i, ln in enumerate(lines):
        if _ERROR_LINE.search(ln):
            a = max(0, i - context)
            b = i + 1
            # Pull in following stack frames so the location is included.
            while b < len(lines) and (_FRAME.match(lines[b]) or lines[b].strip() == ""):
                b += 1
                if b - a >= max_lines:
                    break
            return "\n".join(lines[a:b][:max_lines]).strip()
    return ""


def from_service_logs(log_lines: list[str], *, tail: int = 15) -> str:
    """Given dev-server log lines, return an extracted error block, falling back to
    the last `tail` lines if no recognised error signature is present."""
    text = "\n".join(log_lines)
    err = extract(text)
    if err:
        return err
    return "\n".join(log_lines[-tail:]).strip()


def has_error(log_lines: list[str]) -> bool:
    return bool(extract("\n".join(log_lines)))
