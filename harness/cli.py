"""Command-line entry point for the app-builder harness.

Examples:
    # Build with Claude (default engine)
    appbuilder specs/todo-cli.yaml --workspace workspaces/todo-cli

    # Build with a local LLM via Ollama
    appbuilder specs/landing-page.yaml -w workspaces/landing \\
        --engine local --base-url http://localhost:11434/v1 --model qwen2.5-coder

    # Just run the verification suite (no model, no API key needed)
    appbuilder specs/todo-cli.yaml -w workspaces/todo-cli --check-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from harness.config import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_MODEL,
    EngineConfig,
    HarnessConfig,
)
from harness.spec import SpecError, load_spec
from harness.verifier import run_suite


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="appbuilder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec", help="Path to the YAML build spec")
    p.add_argument("--workspace", "-w", required=True, help="Directory to build the app in")
    p.add_argument("--engine", "-e", choices=["anthropic", "local"], default="anthropic",
                   help="Model backend (default: anthropic)")
    p.add_argument("--model", "-m", default=None,
                   help=f"Model id/name (anthropic default: {DEFAULT_MODEL}; required for local)")
    p.add_argument("--base-url", default=None,
                   help=f"Local OpenAI-compatible endpoint (default: {DEFAULT_LOCAL_BASE_URL})")
    p.add_argument("--api-key-env", default=None,
                   help="Env var holding the API key (anthropic: ANTHROPIC_API_KEY; local: OPENAI_API_KEY)")
    p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (local engine)")
    p.add_argument("--max-repairs", type=int, default=4, help="Repair rounds after the first attempt")
    p.add_argument("--max-turns", type=int, default=80, help="Max agentic turns per engine call")
    # Isolation
    p.add_argument("--isolation", choices=["directory", "worktree"], default="directory",
                   help="Per-build isolation mode (default: directory)")
    p.add_argument("--base-repo", default=None, help="Base git repo for --isolation worktree")
    p.add_argument("--cleanup", action="store_true", help="Remove a worktree workspace when done")
    # Reviewer / Sentry gate
    p.add_argument("--review", action="store_true", help="Enable the reviewer/sentry second gate")
    p.add_argument("--review-focus", choices=["quality", "bugs"], default="quality",
                   help="Reviewer focus: code quality or Sentry-style bug hunting")
    p.add_argument("--reviewer-model", default=None, help="Model for the reviewer (default: builder model)")
    # Learning memory
    p.add_argument("--learn", action="store_true", help="Record and reuse lessons from past builds")
    p.add_argument("--memory", default=None, help="Path to the JSONL lesson store (implies --learn)")
    p.add_argument("--check-only", action="store_true",
                   help="Skip the agent; only run the verification suite against the workspace.")
    p.add_argument("--no-echo", action="store_true", help="Do not stream the agent transcript to stdout")
    return p.parse_args(argv)


def _engine_config(args) -> EngineConfig:
    if args.engine == "local":
        return EngineConfig(
            provider="local",
            model=args.model or "",
            base_url=args.base_url or DEFAULT_LOCAL_BASE_URL,
            api_key_env=args.api_key_env or "OPENAI_API_KEY",
            temperature=args.temperature,
        )
    return EngineConfig(
        provider="anthropic",
        model=args.model or DEFAULT_MODEL,
        api_key_env=args.api_key_env or "ANTHROPIC_API_KEY",
        temperature=args.temperature,
    )


def _check_only(spec) -> int:
    report = run_suite(spec.checks, stop_on_failure=True)
    print(report.to_feedback() or "(no checks defined)")
    print("\nRESULT:", "PASS" if report.ok else "FAIL")
    return 0 if report.ok else 1


def _preflight(engine: EngineConfig) -> str | None:
    """Return an error message if the engine can't run, else None."""
    if engine.provider == "anthropic":
        if not os.environ.get(engine.api_key_env):
            return (f"{engine.api_key_env} is not set. Export it, switch to --engine local, "
                    "or use --check-only.")
    else:  # local
        if not engine.model:
            return "the local engine requires --model (the name your server serves, e.g. qwen2.5-coder)."
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    workspace = os.path.abspath(args.workspace)

    try:
        spec = load_spec(args.spec, cwd=workspace)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check_only:
        return _check_only(spec)

    engine = _engine_config(args)
    err = _preflight(engine)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    from harness.agent import build  # lazy: keeps --check-only free of SDK deps

    memory_path = args.memory
    learn = args.learn or bool(memory_path)
    if learn and not memory_path:
        memory_path = os.path.join(os.path.dirname(workspace) or ".", ".appbuilder_lessons.jsonl")

    config = HarnessConfig(
        workspace=workspace,
        engine=engine,
        max_repairs=args.max_repairs,
        max_turns=args.max_turns,
        isolation=args.isolation,
        base_repo=args.base_repo,
        keep_workspace=not args.cleanup,
        enable_review=args.review,
        review_focus=args.review_focus,
        reviewer_model=args.reviewer_model,
        learn=learn,
        memory_path=memory_path,
    )

    gates = "verify" + ("+review:" + args.review_focus if args.review else "")
    print(f"engine: {engine.provider} | model: {engine.model or '(unset)'} | kind: {spec.kind} | "
          f"isolation: {args.isolation} | gates: {gates}")
    result = asyncio.run(build(spec, config, echo=not args.no_echo))

    print("\n" + "=" * 40)
    print(f"BUILD {'SUCCEEDED' if result.ok else 'FAILED'} after {result.rounds} round(s)")
    if result.report and not result.ok:
        print("\nRemaining failures:")
        for f in result.report.failures:
            print(f"  - {f.name} (exit {f.returncode})")
    if result.verdict and not result.verdict.approved:
        print("\nReviewer findings:")
        for f in result.verdict.blocking:
            print(f"  - ({f.severity}) {f.title}")
    if learn:
        print(f"Lessons recorded this build: {result.lessons_learned}")
    print(f"Workspace: {result.workspace or workspace}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
