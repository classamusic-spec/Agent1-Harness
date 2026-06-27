# Design / a11y / performance checks

Headless verification scripts you wire into a spec's `verification` so front-end
quality is a hard gate, not just a prompt.

## Setup

```bash
cd checks
npm ci
npx playwright install chromium   # if Chromium isn't already present
```

## Use

```bash
node checks/a11y_audit.mjs        path/to/index.html   # axe-core, fails on critical/serious
node checks/lighthouse_audit.mjs  path/to/index.html   # Lighthouse score budgets
```

In a spec (paths are relative to the build workspace):

```yaml
verification:
  - name: a11y
    command: "node ../../checks/a11y_audit.mjs index.html"
  - name: lighthouse
    command: "node ../../checks/lighthouse_audit.mjs index.html"
    allow_failure: true   # advisory until you've tuned budgets
```

Budgets for Lighthouse are overridable: `LH_PERF=0.9 LH_A11Y=0.95 node checks/lighthouse_audit.mjs ...`.

> These need Node.js and Chromium. They're templates — adapt selectors, routes,
> and budgets to your app. For React/React Native or SwiftUI, replace these with
> the platform's own tooling (`tsc`/`eslint`/`vitest`, `swift test`/`xcodebuild test`).
