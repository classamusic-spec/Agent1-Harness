# Agent1-Harness

An **agent harness that builds applications from a spec** — a master front-end UI
designer and a rigorous backend engineer — that runs on **Claude or your own
local LLM**.

You write a short YAML spec (what to build + how it's verified). The harness
drives the model through a deterministic loop until the app actually builds and
its tests pass:

```
spec ──▶ implement ──▶ VERIFY ──▶ (repair ──▶ VERIFY)* ──▶ done
                          │
                          └── build / typecheck / lint / tests must pass
```

The model writes the code; **the harness decides when it's done** — "done" means
the verification commands exit zero, not that the model said so.

## Two things this adds

### 1. Specialist expertise (design + backend)

The agent's system prompt is composed from a shared engineering core plus
discipline-specific personas, selected by the spec's `kind`:

| `kind` | Persona |
|---|---|
| `frontend` | master front-end UI/UX designer (design system, typography & spacing scales, semantic tokens, responsive, WCAG AA a11y, tasteful motion, no generic "AI" aesthetics) |
| `backend` / `cli` / `api` / `library` | rigorous backend engineer (clear architecture, validation at boundaries, specific errors, security basics, tests) |
| `fullstack` (default) | both |

The personas live in `harness/personas.py`. Crucially, design quality is also
**verifiable**: the example frontend spec checks for design tokens, responsive
`@media` queries, a single `<h1>`, semantic landmarks, and a viewport meta — so
"looks designed" isn't left to vibes.

### 2. Plug in your local LLM

The harness has a pluggable **engine** layer (`harness/engines/`):

- **`anthropic`** (default) — drives the Claude Agent SDK with its built-in tools.
- **`local`** — our own tool-calling loop against any **OpenAI-compatible**
  server: Ollama, LM Studio, vLLM, llama.cpp's server, text-generation-webui.

The deterministic verification gate and the personas are **identical** across
engines — only the model behind the loop changes. For the local engine we
implement a workspace-confined toolbox (`read_file` / `write_file` / `edit_file`
/ `list_dir` / `search` / `run_bash` / `verify`) and hand the model OpenAI
function-tool schemas.

> The local model must support OpenAI-style tool calling. Capable code models
> (e.g. Qwen2.5-Coder, Llama 3.1+, DeepSeek-Coder) work well; very small models
> are unreliable at multi-step tool use.

## Robustness (the design)

| Principle | Where it lives |
|---|---|
| **Verification gate, not vibes** — green checks required, run deterministically between turns | `harness/verifier.py`, loop in `harness/agent.py` |
| **Engine-agnostic core** — same gate + personas for Claude or local | `harness/engines/`, `harness/personas.py` |
| **Isolation** — the agent works only inside an ephemeral workspace dir | `cwd` + `harness/permissions.py` + `harness/localtools.py` |
| **Tight tools** — focused allowlist; shell commands screened identically on both engines | `harness/permissions.py` (`screen_command`) |
| **Bounded autonomy** — capped repair rounds and agentic turns | `HarnessConfig` |
| **Reviewable output** — work lands in a workspace you can diff; never pushed | `.gitignore`, `git push` blocked |

## Layout

```
harness/
  verifier.py     # verification gate — runs commands, structured results (no LLM)
  spec.py         # load/validate the YAML spec, incl. `kind` (no LLM)
  personas.py     # specialist system prompts (frontend design / backend rigor)
  prompts.py      # build + repair prompts
  permissions.py  # sandbox: confine writes, screen dangerous commands
  localtools.py   # workspace-confined toolbox for the local engine
  tools.py        # custom `verify` MCP tool for the Anthropic engine
  config.py       # EngineConfig + limits
  engines/
    base.py            # Engine interface + factory
    anthropic_engine.py
    local_engine.py    # OpenAI-compatible tool-calling loop
  agent.py        # engine-agnostic orchestrator loop
  cli.py          # `appbuilder` entry point
specs/            # todo-cli.yaml (cli), landing-page.yaml (frontend)
tests/            # offline tests for verifier, spec, localtools, personas
```

## Setup

```bash
pip install -e .            # Claude engine only
pip install -e ".[local]"   # add the local-LLM engine (openai client)
pip install -e ".[dev]"     # for the tests
```

Python 3.10+.

## Usage

```bash
# Build with Claude (default)
export ANTHROPIC_API_KEY=sk-ant-...
appbuilder specs/landing-page.yaml -w workspaces/landing

# Build with a local model via Ollama
appbuilder specs/landing-page.yaml -w workspaces/landing \
    --engine local --base-url http://localhost:11434/v1 --model qwen2.5-coder

# LM Studio instead (default port differs)
appbuilder specs/todo-cli.yaml -w workspaces/todo \
    --engine local --base-url http://localhost:1234/v1 --model your-model

# Just run the verification suite (no model, no key)
appbuilder specs/landing-page.yaml -w workspaces/landing --check-only
```

Flags: `--engine {anthropic,local}`, `--model`, `--base-url`, `--api-key-env`,
`--temperature`, `--max-repairs`, `--max-turns`, `--check-only`, `--no-echo`.
Exit code is `0` on pass, non-zero otherwise — CI-friendly.

## Writing a spec

```yaml
name: my-app
kind: frontend          # frontend | backend | fullstack | cli | api | library
language: html/css/js
description: |
  Plain-language description of what to build.
constraints:
  - "No frameworks; plain HTML/CSS/JS."
verification:
  - name: files
    command: "test -f index.html"
  - name: structure
    command: "python3 -c \"assert 'var(' in open('styles.css').read()\""
```

Make `verification` the real contract — and for frontend work, encode design
expectations there (tokens, responsiveness, a11y) so the gate enforces them.

## Tests

```bash
pytest        # 30 offline tests: verifier, spec, localtools, personas
```

## Limitations / next steps

- The Anthropic and local loops are exercised live with a key/server; the
  trust-critical deterministic core is fully unit-tested offline.
- Local tool-calling reliability depends on the model — prefer strong code models.
- Next: container/worktree isolation per build; richer design checks (headless
  Playwright/Lighthouse, axe a11y) wired as verification commands; a reviewer
  agent as a second gate; cost/time budgets surfaced from engine usage.
