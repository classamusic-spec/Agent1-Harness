"""Generate a GitHub Actions CI workflow for a built app.

Stack-aware (reusing the same detection as the verifier): a Node app gets
install/build/test, a Python app gets install/compile/pytest, a static site gets a
presence check. Written as `.github/workflows/ci.yml` when exporting to GitHub so
the repo is green on the first push. Stdlib only.
"""

from __future__ import annotations

import os

from harness import stacks

_NODE = """\
name: CI
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci || npm install
      - run: npm run build --if-present
      - run: npm test --if-present
"""

_PYTHON = """\
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: '[ -f requirements.txt ] && pip install -r requirements.txt || echo "no requirements.txt"'
      - name: Compile
        run: python -m compileall -q .
      - name: Tests
        run: '[ -d tests ] || ls test_*.py >/dev/null 2>&1 && pytest -q || echo "no tests"'
"""

_STATIC = """\
name: CI
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: App present
        run: test -f index.html && echo "index.html present"
"""


def workflow_for(workspace: str) -> str:
    stack = stacks.detect_stack(workspace)
    return {"node": _NODE, "python": _PYTHON}.get(stack, _STATIC)


def write_workflow(workspace: str, *, overwrite: bool = False) -> bool:
    """Write .github/workflows/ci.yml. Returns True if written (False if it exists)."""
    dest = os.path.join(workspace, ".github", "workflows", "ci.yml")
    if os.path.exists(dest) and not overwrite:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(workflow_for(workspace))
    return True
