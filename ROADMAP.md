# Improvement Roadmap

Where the agent is strong today, and the highest-leverage things to add next.
Ordered by impact-to-effort.

## Today (done)
- Deterministic verification gate; engine-agnostic loop (Claude **or** local LLM).
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
  desk and animates with the build pipeline (idle / building / passed / failed).
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
