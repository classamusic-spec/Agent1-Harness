# Improvement Roadmap

Where the agent is strong today, and the highest-leverage things to add next.
Ordered by impact-to-effort.

## Today (done)
- Deterministic verification gate; engine-agnostic loop (Claude **or** local LLM).
- Specialist personas: frontend design, backend rigor, React/React Native, SwiftUI.
- Reviewer / Sentry second gate (independent, structured verdict).
- Learns from mistakes: mechanical + model-driven reflection lessons.
- Convergence guards: stall detection + wall-clock budget.
- Per-build isolation (directory / git worktree).
- Clean web console + design-check templates (Playwright/axe/Lighthouse).

## Next — correctness & convergence
1. **Diff-aware repair** — show the model a unified diff of what changed between
   rounds + the *delta* in failures, so repairs target regressions precisely.
2. **Escalation policy** — on repeated stalls, automatically raise effort /
   switch to a stronger model / spawn a fresh-context "fixer" before giving up.
3. **Test-first option** — generate the verification/tests from the spec first,
   get them approved, then build against them (red→green).

## Next — quality & trust
4. **Multi-reviewer panel** — run quality + sentry + a11y reviewers in parallel
   and require majority/no-blocker to pass (cheap on a local model).
5. **Patch-level review** — reviewer reads the diff, not just the tree, for
   faster, more focused verdicts on iterative builds.
6. **Security pass** — a dedicated reviewer focus for secrets, injection,
   unsafe deserialization, dependency risks.

## Next — isolation & ops
7. **Docker isolation** — run builds (and their shell) in a throwaway container,
   not just a directory/worktree.
8. **Cost/time telemetry** — surface token + wall-clock usage per build from the
   engine and enforce hard budgets; show it in the console.
9. **Resumable builds** — persist loop state so a build can be paused/resumed.

## Next — reach & DX
10. **Project scaffolds** — first-class starters for React (Vite), React Native
    (Expo), and SwiftUI (SwiftPM) so the model edits a known-good base.
11. **Spec authoring in the UI** — create/edit specs and pick `kind` from the
    console; live-preview the composed persona.
12. **Artifact gallery** — browse/download built apps and screenshots from the UI.
13. **Human-in-the-loop approval gate** — optional pause for sign-off before a
    build is accepted or published (commit/PR).
