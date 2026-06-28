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
    p.add_argument("spec", nargs="?", help="Path to the YAML build spec (omit when using --resume)")
    p.add_argument("--workspace", "-w", help="Directory to build the app in")
    p.add_argument("--checkpoint", default=None,
                   help="Write a resumable checkpoint to this path each round")
    p.add_argument("--resume", default=None,
                   help="Resume a build from a checkpoint file (no spec needed)")
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
    # Exec sandbox
    p.add_argument("--sandbox", choices=["host", "docker"], default="host",
                   help="Where verification/shell commands run (default: host)")
    p.add_argument("--docker-image", default="python:3.12-slim",
                   help="Image for --sandbox docker")
    # Test-first
    p.add_argument("--test-first", action="store_true",
                   help="Derive the verification suite from the spec before building (red->green)")
    # Human-in-the-loop approval
    p.add_argument("--approve-plan", action="store_true",
                   help="Pause for sign-off on the test-first suite before building")
    p.add_argument("--approve-build", action="store_true",
                   help="Pause for sign-off on the finished build before accepting")
    # Budgets
    p.add_argument("--token-budget", type=int, default=None,
                   help="Stop the build once this many tokens are used")
    # Reviewer / Sentry gate
    p.add_argument("--review", action="store_true", help="Enable the reviewer/sentry second gate")
    p.add_argument("--review-focus", choices=["quality", "bugs", "a11y", "security"], default="quality",
                   help="Single reviewer focus: quality, bug hunting, accessibility, or security")
    p.add_argument("--review-panel", default=None,
                   help="Run a parallel reviewer panel, e.g. 'quality,bugs,a11y,security' (implies --review)")
    p.add_argument("--reviewer-model", default=None, help="Model for the reviewer (default: builder model)")
    # Learning memory
    p.add_argument("--learn", action="store_true", help="Record and reuse lessons from past builds")
    p.add_argument("--memory", default=None, help="Path to the JSONL lesson store (implies --learn)")
    p.add_argument("--no-reflect", action="store_true",
                   help="Disable model-driven reflection (use mechanical lessons only)")
    # Loop convergence guards
    p.add_argument("--stall-limit", type=int, default=3,
                   help="Consecutive identical failing rounds before escalate/stop (default: 3)")
    p.add_argument("--max-escalations", type=int, default=1,
                   help="Fresh-fixer attempts on a stall before giving up (default: 1)")
    p.add_argument("--escalation-model", default=None,
                   help="Stronger model for the escalation fixer (default: builder model)")
    p.add_argument("--no-diff", action="store_true",
                   help="Disable diff-aware repair prompts")
    p.add_argument("--deadline", type=float, default=None,
                   help="Overall wall-clock budget in seconds (default: unlimited)")
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


def _summarize(result, *, workspace=None, learn=False) -> int:
    print("\n" + "=" * 40)
    print(f"BUILD {'SUCCEEDED' if result.ok else 'FAILED'} ({result.stop_reason}) "
          f"after {result.rounds} round(s)")
    if result.progress:
        print(f"Failing checks per round: {result.progress}")
    if result.escalations:
        print(f"Escalations used: {result.escalations}")
    print(f"Telemetry: {result.tokens_used} tokens · {result.elapsed_seconds:.1f}s")
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Resume mode: continue a build from its checkpoint (no spec/workspace needed).
    if args.resume:
        from harness.agent import resume
        print(f"Resuming from checkpoint: {args.resume}")
        result = asyncio.run(resume(args.resume, echo=not args.no_echo))
        return _summarize(result)

    if not args.spec or not args.workspace:
        print("error: a spec and --workspace are required (or use --resume CHECKPOINT)", file=sys.stderr)
        return 2
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
        exec_sandbox=args.sandbox,
        docker_image=args.docker_image,
        test_first=args.test_first,
        enable_review=args.review or bool(args.review_panel),
        review_focus=args.review_focus,
        review_panel=[f.strip() for f in args.review_panel.split(",") if f.strip()] if args.review_panel else [],
        reviewer_model=args.reviewer_model,
        learn=learn,
        memory_path=memory_path,
        reflect=not args.no_reflect,
        stall_limit=args.stall_limit,
        deadline_seconds=args.deadline,
        diff_aware=not args.no_diff,
        max_escalations=args.max_escalations,
        escalation_model=args.escalation_model,
        approve_plan=args.approve_plan,
        approve_build=args.approve_build,
        max_tokens_budget=args.token_budget,
        checkpoint_path=args.checkpoint,
    )

    approval = None
    if args.approve_plan or args.approve_build:
        from harness.approval import CLIApproval
        approval = CLIApproval()

    if args.review_panel:
        gates = "verify+panel:" + args.review_panel
    elif args.review:
        gates = "verify+review:" + args.review_focus
    else:
        gates = "verify"
    extras = []
    if args.test_first:
        extras.append("test-first")
    if args.sandbox != "host":
        extras.append(f"sandbox:{args.sandbox}")
    print(f"engine: {engine.provider} | model: {engine.model or '(unset)'} | kind: {spec.kind} | "
          f"isolation: {args.isolation} | gates: {gates}"
          + (" | " + " ".join(extras) if extras else ""))
    result = asyncio.run(build(spec, config, echo=not args.no_echo, approval=approval))
    return _summarize(result, workspace=workspace, learn=learn)


if __name__ == "__main__":
    raise SystemExit(main())
