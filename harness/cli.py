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

    config = HarnessConfig(
        workspace=workspace,
        engine=engine,
        max_repairs=args.max_repairs,
        max_turns=args.max_turns,
    )

    print(f"engine: {engine.provider} | model: {engine.model or '(unset)'} | kind: {spec.kind}")
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
