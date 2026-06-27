# Agent1-Harness

An **agent harness that builds applications from a spec**, built on the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview).

You write a short YAML spec describing the app and a verification suite. The
harness drives Claude through a deterministic loop until the app actually builds
and its tests pass:

```
spec ──▶ plan ──▶ implement ──▶ VERIFY ──▶ (repair ──▶ VERIFY)* ──▶ done
                                  │
                                  └── build / typecheck / lint / tests must pass
```

The model writes the code; **the harness decides when it's done** — and "done"
means the verification commands exit zero, not that the model said so.

## Why this is robust (the design)

| Principle | Where it lives |
|---|---|
| **Verification gate, not vibes** — green checks are required, run deterministically between turns | `harness/verifier.py`, the loop in `harness/agent.py` |
| **Isolation** — the agent works only inside an ephemeral workspace dir | `cwd` in `harness/agent.py`, write-confinement in `harness/permissions.py` |
| **Tight, typed tools** — a focused allowlist plus one custom `verify` tool | `harness/config.py`, `harness/tools.py` |
| **Bounded autonomy + escalation** — capped repair rounds and agentic turns; dangerous shell commands blocked | `HarnessConfig`, `harness/permissions.py` |
| **Reviewable output** — work lands in a workspace directory you can diff; the harness never pushes | `.gitignore` excludes `workspaces/`; `git push` is blocked in the sandbox |
| **Observability** — full transcript captured; verification report printed each round | `BuildResult.transcript`, `VerificationReport.to_feedback()` |

## Layout

```
harness/
  verifier.py     # the verification gate — runs commands, returns structured results (no LLM)
  spec.py         # load/validate the YAML build spec (no LLM)
  prompts.py      # system + build + repair prompts
  permissions.py  # sandbox: confine writes, block dangerous commands
  tools.py        # custom in-process `verify` tool exposed to the agent (MCP)
  config.py       # model, limits, allowed tools
  agent.py        # the orchestrator loop (the only module that calls the SDK)
  cli.py          # `appbuilder` entry point
specs/todo-cli.yaml  # example spec
tests/               # offline tests for the verifier + spec loader
```

`verifier.py` and `spec.py` are deliberately LLM-free so the trust-critical
parts are unit-tested offline.

## Setup

```bash
pip install -e .            # or: pip install -e ".[dev]" for the tests
export ANTHROPIC_API_KEY=sk-ant-...
```

Requires Python 3.10+.

## Usage

Build the example to-do CLI:

```bash
appbuilder specs/todo-cli.yaml --workspace workspaces/todo-cli
```

Useful flags:

- `--model claude-opus-4-8` — model id (or aliases `opus` / `sonnet` / `haiku`)
- `--max-repairs 4` — repair rounds after the first attempt
- `--check-only` — skip the agent; just run the verification suite against the
  workspace. **Needs no API key** — handy for inspecting what the gate sees.
- `--no-echo` — don't stream the transcript

Exit code is `0` when verification passes, non-zero otherwise — so you can wire
it into CI.

## Writing a spec

```yaml
name: my-app
language: python
description: |
  Plain-language description of what to build.
constraints:
  - "Standard library only."
verification:
  - name: compile
    command: "python -m compileall -q ."
  - name: tests
    command: "python -m pytest -q"
```

The `verification` commands run inside the workspace. Make them the real
contract: if they pass, the app is acceptable.

## Tests

```bash
pytest
```

These cover the verifier and spec loader end-to-end without touching the API.

## Limitations / next steps

This is a working prototype, not a hosted product. Natural extensions:

- **Stronger isolation** — run each build in a container or a fresh git worktree
  instead of just a directory.
- **Per-file staleness checks** and a richer plan artifact persisted to disk.
- **Cost/time budgets** surfaced from the SDK usage and enforced in the loop.
- **A reviewer agent** as a second gate before a human merges.
- For server-managed, hosted builds, see the SDK's **Managed Agents** surface,
  which provisions a per-session container and can mount GitHub repos directly.
