"""Human-in-the-loop approval gates.

The loop can pause for a human decision at two points:
  - "plan"  : sign off on the test-first verification suite before building.
  - "build" : sign off on the finished build before it's accepted.

An ApprovalGate turns those pauses into a decision. Default is AutoApprove (no
pause), so existing behavior is unchanged. The CLI uses a stdin prompt; the web
console uses a gate that waits for a button click.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


@dataclass
class Decision:
    approved: bool
    message: str = ""


class ApprovalGate(Protocol):
    async def request(self, kind: str, payload: dict) -> Decision: ...


class AutoApprove:
    """Always approves; the loop runs autonomously."""

    async def request(self, kind: str, payload: dict) -> Decision:
        return Decision(True)


class CallbackApproval:
    """Delegates to a (sync or async) callback. Used by the server and tests."""

    def __init__(self, fn: Callable[[str, dict], "Decision | Awaitable[Decision]"]):
        self._fn = fn

    async def request(self, kind: str, payload: dict) -> Decision:
        result = self._fn(kind, payload)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, Decision):
            return result
        if isinstance(result, bool):
            return Decision(result)
        return Decision(bool(result))


class CLIApproval:
    """Prompt for approval on stdin (terminal use)."""

    async def request(self, kind: str, payload: dict) -> Decision:
        print(f"\n[approval] {kind} needs sign-off:")
        if kind == "plan":
            for c in payload.get("checks", []):
                print(f"    - {c[0]}: {c[1]}")
        else:
            print(f"    workspace: {payload.get('workspace')}  ({payload.get('reason')})")
        ans = (await asyncio.to_thread(input, "Approve? [y/N] (optional 'n: reason'): ")).strip()
        approved = ans[:1].lower() == "y"
        message = ans.split(":", 1)[1].strip() if ":" in ans else ""
        return Decision(approved, message)
