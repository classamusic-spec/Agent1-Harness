# Improvement Roadmap

Where the agent is strong today, and the highest-leverage things to add next.
Ordered by impact-to-effort.

## Today (done)
- **Multi-agent decomposition** — split a full-stack build across role specialists:
  an architect writes a shared **contract** (API + data model + file ownership),
  then a **backend** agent and a **frontend** agent build in parallel on isolated
  worktrees, each running the normal verify/repair loop. Results are **merged by
  ownership** (disjoint paths → conflict-free), then an **integration** agent is
  driven to green on the full app suite. `harness/multiagent.py`, `--multi`
  (`--multi-sequential`) / Studio "Multi-agent".
- **Ship it** — package a built app for deployment: a stack-aware `Dockerfile`
  (node / python / static), a `docker-compose.yml` (port + `.env` wired in), a
  `.dockerignore`, and a downloadable `.zip` (excludes secrets/DBs/caches, ships
  `.env.example`, materialises a local `.env`). `harness/ship.py`, `--ship` /
  `--ship-zip`, and a Studio **Ship ↧** dialog (preview files, add to project,
  download zip). Verified live: the generated artifacts `docker build` + `docker
  compose up` and serve the app (migrations run on boot).
- **Data layer** — a SQLite migration runner (`harness/migrations.py`: ordered,
  idempotent `migrations/*.sql`, tracked in `schema_migrations`, `seed()` + CLI)
  and `.env`/secrets handling (`harness/env.py`: tolerant `load_dotenv`,
  `generate_env` with a random secret, `ensure_env` materialises `.env` from
  `.env.example`). The workspace `.env` is injected into every verification check
  and the dev server (runner `env=`), so migrations and the running app share one
  config. New **`python-db`** scaffold: stdlib `http.server` + `sqlite3` notes API,
  `migrations/001_init.sql`, `migrate.py`, `.env.example`, with checks that apply
  migrations, boot the server, and POST a row — all dependency-free + offline.
- **Milestone planner** — `--plan` decomposes a big app into an ordered plan
  (schema → API → UI → integration) and drives each milestone to green before the
  next (own build/verify/repair loop, fresh context, shared workspace); the final
  milestone is gated on the full app suite. `harness/planner.py`, `--plan` /
  Studio "Plan milestones".
- **Scaffolds** — start from a known-good base (`static`, `python-api` stdlib
  full-stack, `vite-react`, `fastapi`) that owns its run command + checks, so the
  agent edits a working app and the full-stack gate has a real toolchain.
  `harness/scaffolds.py`, `--scaffold` / Studio "Start from".
- **Real full-stack verification** — stack-aware default checks (node:
  install/build/`tsc`/eslint/tests; python: install/`pytest`; static) plus
  **server-backed checks** (`needs_server`): boot the dev server, substitute
  `$APP_URL`, smoke/e2e/contract-test the running app, tear it down; failures
  attach the server log tail. `harness/stacks.py` + `harness/fullstack.py`,
  `checks/e2e_smoke.mjs`, `--run` / spec `run:` / `needs_server:`.
- **Runtime — live full-stack preview** — run the project's real dev server
  (`npm run dev` / `uvicorn` / static, auto-detected), health-check it, and
  reverse-proxy the Studio preview to it; server logs stream into the console.
  `harness/runtime.py` + Run/Stop in Studio. (Foundation for full-stack: real
  toolchain verification, scaffolds, and a milestone planner build on this.)
- **Studio (Replit / Lovable-style)** — describe an app → it builds → **live
  preview** (Desktop / Mobile device frame, reload, Run tests) → **chat to
  iterate** (one engine turn on the workspace, re-verified). A **project
  switcher** jumps between built apps and a **Stop** button cancels a run
  (killing the engine turn immediately for changes; reload re-attaches to a live
  job). **Version history + diffs** snapshot every turn — review a colorized
  per-version diff and **Restore** any version. Freeform prompts become
  first-class specs.
- **Glassmorphic premium UI** — a gradient-mesh backdrop, translucent frosted
  cards, gradient-accent primary; theme switcher (Auto / Light / Dark glass +
  Solid), reduced-motion aware. A↔B version diff compare.
- **Devtools console** — the previewed app's console/errors/fetches stream into a
  panel under the preview, captured via an injected agent (preview-only).
- **Vision (image → UI)** — reference a screenshot when building: a local vision
  model writes a design brief for the coder (two-stage); a multimodal coder reads
  the image directly (Claude Code via the staged file, or a **local VL model
  inline** with `--multimodal`); and a **visual-match loop** (`--visual-check`)
  screenshots the build, vision-compares it to the reference, and repairs the
  differences. `harness/vision.py` + `harness/screenshot.py`, CLI flags, Studio
  upload + toggles.
- **Claude Code (CLI) engine** — plug in your installed, authenticated `claude`
  CLI as the builder (no API key/SDK). Plus `local` presets for GLM / MiniMax /
  Qwen and any OpenAI-compatible server.
- Deterministic verification gate; engine-agnostic loop (Claude SDK, **Claude
  Code CLI**, **or** local LLM).
- Specialist personas: frontend design, backend rigor, React/React Native, SwiftUI.
- Reviewer / Sentry second gate (independent, structured verdict) — single focus
  or a **parallel panel** (quality + bugs + a11y), pass only if no blocker/major.
- Learns from mistakes: mechanical + model-driven reflection lessons.
- Convergence guards: stall detection + wall-clock budget.
- **Diff-aware repair** — repairs see a unified diff of the last change + the failure delta.
- **Stall escalation** — a fresh-context "fixer" (optionally a stronger model) on a stall.
- Per-build isolation (directory / git worktree).
- **Exec sandbox** — run verification/shell commands on the host or inside a
  throwaway **Docker** container with the workspace bind-mounted.
- **Test-first (red→green)** — derive the verification suite from the spec before
  building, then drive the build to green against it.
- **Human-in-the-loop approval** — `--approve-plan` / `--approve-build` pause for
  sign-off (stdin or an Approve/Reject banner in the console). **Feedback-driven**:
  rejecting with a message feeds it back as a targeted repair and the loop continues.
- **Cost/time telemetry + budgets** — tokens + wall-clock per build, with **live
  token & time budget meters** in the console; `--token-budget` / `--deadline`
  stop the loop when exceeded.
- **Resumable builds** — `--checkpoint` / `--resume`; the durable workspace plus a
  JSON checkpoint let an interrupted build continue to green (Resume in the Gallery).
- **Live run controls** — pause / cancel a running build from the console; pause
  leaves a checkpoint so it's resumable.
- **Live run controls** — pause / cancel a running build from the console.
- **3D Office (three.js)** — a futuristic tab where an agent character works at a
  desk beside an **AI rig** (GPU rack); its fans spin and cards glow when a model
  is working, and the scene animates with the build pipeline (idle / building /
  passed / failed).
- **ChatGPT / Hermes-style console** — a left nav rail + roomy content shell with
  a calm palette and automatic light/dark.
- **CI** — GitHub Actions runs the full suite on Python 3.10–3.12 every push/PR.
- Web console with tabs: Build, **New Spec** (authoring), **Gallery** (artifact
  preview + Resume), a **live Workspace view**, and the **3D Office**.
- Design-check templates (Playwright/axe/Lighthouse).

## Next — correctness & convergence
1. **Security review focus** — a dedicated reviewer for secrets, injection,
   unsafe deserialization, and dependency risk.

## Next — quality & trust
4. **Multi-reviewer panel** — run quality + sentry + a11y reviewers in parallel
   and require majority/no-blocker to pass (cheap on a local model).
5. **Patch-level review** — reviewer reads the diff, not just the tree, for
   faster, more focused verdicts on iterative builds.
6. **Security pass** — a dedicated reviewer focus for secrets, injection,
   unsafe deserialization, dependency risks.

## Next — isolation & ops
7. **Container isolation hardening** — non-root, read-only rootfs, capped CPU/mem
   for the Docker exec sandbox; per-build network policy.

## Next — reach & DX
10. **Project scaffolds** — first-class starters for React (Vite), React Native
    (Expo), and SwiftUI (SwiftPM) so the model edits a known-good base.
11. **Spec authoring in the UI** — create/edit specs and pick `kind` from the
    console; live-preview the composed persona.
12. **Artifact gallery** — browse/download built apps and screenshots from the UI.
13. **Human-in-the-loop approval gate** — optional pause for sign-off before a
    build is accepted or published (commit/PR).
