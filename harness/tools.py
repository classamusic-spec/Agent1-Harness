"""Custom in-process tools exposed to the build agent via the SDK's MCP support.

We give the agent a `verify` tool so it can self-check before yielding the turn.
The harness still runs the *authoritative* verification deterministically between
turns (see harness.agent) — this tool is a convenience that tends to reduce the
number of repair rounds, not the source of truth.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from harness.spec import Spec
from harness.verifier import run_suite

MCP_SERVER_NAME = "harness"
VERIFY_TOOL = f"mcp__{MCP_SERVER_NAME}__verify"


def build_tool_server(spec: Spec) -> Any:
    """Create an in-process MCP server exposing the spec's verification suite."""

    @tool(
        "verify",
        "Run the project's verification suite (build, typecheck, lint, tests) and "
        "return the results. Call this before finishing to confirm your work passes.",
        {"type": "object", "properties": {}},
    )
    async def verify(args: dict) -> dict:
        report = run_suite(spec.checks, stop_on_failure=True)
        status = "ALL CHECKS PASSED" if report.ok else "VERIFICATION FAILED"
        body = report.to_feedback() or "(no checks defined)"
        return {"content": [{"type": "text", "text": f"{status}\n\n{body}"}]}

    return create_sdk_mcp_server(name=MCP_SERVER_NAME, version="0.1.0", tools=[verify])
