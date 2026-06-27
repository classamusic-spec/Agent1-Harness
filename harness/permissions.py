"""Permission gate: keep the agent inside its sandbox and block dangerous ops.

Safety in this harness is layered:

  1. `cwd` confines the agent to an isolated workspace directory.
  2. `allowed_tools` is an allowlist of built-in tools.
  3. This callback is the last line of defence: it denies writes outside the
     workspace and refuses obviously destructive / exfiltrating shell commands.

The Claude Agent SDK's permission-callback return type has shifted across
versions (typed `PermissionResultAllow/Deny` vs. a plain dict). We detect the
typed results if available and fall back to the documented dict shape, so the
same callback works regardless of the installed SDK version.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

try:  # Preferred: typed result objects from the SDK.
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # type: ignore

    def _allow() -> Any:
        return PermissionResultAllow()

    def _deny(reason: str) -> Any:
        return PermissionResultDeny(message=reason)

except Exception:  # Fallback: documented dict shape.

    def _allow() -> Any:
        return {"allowed": True}

    def _deny(reason: str) -> Any:
        return {"allowed": False, "reason": reason}


# Shell fragments we never want the build agent to run. This is a defence in
# depth backstop, not a substitute for the sandbox — the agent already runs in
# an isolated workspace with no production credentials.
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/(?!\w)",        # rm -rf / (root), but allow rm -rf ./build etc.
    r"\bsudo\b",
    r":\(\)\s*\{",                  # fork bomb
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\bgit\s+push\b",             # never push from inside a build
    r"\bcurl\b[^|]*\|\s*(sh|bash)",  # curl | sh
    r"\bwget\b[^|]*\|\s*(sh|bash)",
    r"\bchmod\s+-R\s+777\s+/",
]
_DANGEROUS_RE = [re.compile(p) for p in _DANGEROUS_PATTERNS]

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _within(path: str, root: str) -> bool:
    try:
        real_root = os.path.realpath(root)
        real_path = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        return real_path == real_root or real_path.startswith(real_root + os.sep)
    except OSError:
        return False


def is_within(path: str, root: str) -> bool:
    """Public: True if `path` resolves to inside `root` (or is `root` itself)."""
    return _within(path, root)


def screen_command(command: str) -> str | None:
    """Public: return a denial reason if `command` matches a dangerous pattern, else None."""
    for rx in _DANGEROUS_RE:
        if rx.search(command):
            return (
                f"command blocked by safety policy (matched {rx.pattern!r}). "
                "Stay inside the workspace and avoid destructive or networked commands."
            )
    return None


def make_permission_callback(workspace: str) -> Callable[..., Any]:
    """Return an async `can_use_tool(tool_name, tool_input, context)` callback."""

    async def can_use_tool(tool_name: str, tool_input: dict, context: Any = None) -> Any:
        # Gate file writes to the workspace.
        if tool_name in _WRITE_TOOLS:
            target = (tool_input or {}).get("file_path") or (tool_input or {}).get("path")
            if target and not _within(str(target), workspace):
                return _deny(f"writes are confined to the workspace ({workspace}); refused {target!r}")
            return _allow()

        # Screen shell commands.
        if tool_name == "Bash":
            command = str((tool_input or {}).get("command", ""))
            for rx in _DANGEROUS_RE:
                if rx.search(command):
                    return _deny(
                        f"command blocked by safety policy (matched {rx.pattern!r}). "
                        "Stay inside the workspace and avoid destructive or networked commands."
                    )
            return _allow()

        return _allow()

    return can_use_tool
