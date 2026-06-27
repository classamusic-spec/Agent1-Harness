"""Command-line entry point for the app-builder harness.

Examples:
    # Build an app from a spec into ./workspaces/todo-cli
    appbuilder specs/todo-cli.yaml --workspace workspaces/todo-cli

    # Just run the verification suite against an existing workspace (no API needed)
    appbuilder specs/todo-cli.yaml --workspace workspaces/todo-cli --check-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from harness.config import DEFAULT_MODEL, HarnessConfig
from harness.spec import SpecError, load_spec
from harness.verifier import run_suite


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="appbuilder", description=__doc__)
    p.add_argument("spec", help="Path to the YAML build spec")
    p.add_argument("--workspace", "-w", required=True, help="Directory to build the app in")
    p.add_argument("--model", "-m", default=DEFAULT_MODEL, help=f"Model id (default: {DEFAULT_MODEL})")
    p.add_argument("--max-repairs", type=int, default=4, help="Repair rounds after the first attempt")
    p.add_argument("--max-turns", type=int, default=80, help="Max agentic turns per SDK call")
    p.add_argument(
        "--check-only",
        action="store_true",
        help="Skip the agent; only run the verification suite against the workspace.",
    )
    p.add_argument("--no-echo", action="store_true", help="Do not stream the agent transcript to stdout")
    return p.parse_args(argv)


def _run_check_only(spec, workspace: str) -> int:
    report = run_suite(spec.checks, stop_on_failure=True)
    print(report.to_feedback() or "(no checks defined)")
    print("\nRESULT:", "PASS" if report.ok else "FAIL")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    workspace = os.path.abspath(args.workspace)

    try:
        spec = load_spec(args.spec, cwd=workspace)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check_only:
        return _run_check_only(spec, workspace)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: ANTHROPIC_API_KEY is not set. Export it, or use --check-only to "
            "run verification without the agent.",
            file=sys.stderr,
        )
        return 2

    # Import the agent lazily so --check-only works without the SDK installed.
    from harness.agent import build

    config = HarnessConfig(
        workspace=workspace,
        model=args.model,
        max_repairs=args.max_repairs,
        max_turns=args.max_turns,
    )

    result = asyncio.run(build(spec, config, echo=not args.no_echo))

    print("\n" + "=" * 40)
    print(f"BUILD {'SUCCEEDED' if result.ok else 'FAILED'} after {result.attempts} attempt(s)")
    if result.report and not result.ok:
        print("\nRemaining failures:")
        for f in result.report.failures:
            print(f"  - {f.name} (exit {f.returncode})")
    print(f"Workspace: {workspace}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
