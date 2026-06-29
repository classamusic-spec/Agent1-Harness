#!/usr/bin/env bash
# Lathe — one-command setup for macOS (Apple Silicon or Intel).
#
#   ./scripts/install-mac.sh                # Python venv + harness (+ local engine)
#   ./scripts/install-mac.sh --with-ollama   # also install Ollama + pull a coder model
#   ./scripts/install-mac.sh --with-claude   # also install the Claude Code CLI
#   ./scripts/install-mac.sh --with-menubar  # also build the ✦ menu-bar app
#
# Safe to re-run. It never overwrites your work; it only sets up a .venv and deps.
set -euo pipefail

WITH_OLLAMA=0
WITH_CLAUDE=0
WITH_MENUBAR=0
for arg in "$@"; do
  case "$arg" in
    --with-ollama) WITH_OLLAMA=1 ;;
    --with-claude) WITH_CLAUDE=1 ;;
    --with-menubar) WITH_MENUBAR=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
say() { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m!\033[0m %s\n" "$*"; }

[ "$(uname -s)" = "Darwin" ] || warn "This script targets macOS; continuing anyway."

# 1) Python 3.10+ -----------------------------------------------------------
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    v=$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
    major=${v%%.*}; minor=${v##*.}
    if [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; then PY="$cand"; break; fi
  fi
done
if [ -z "$PY" ]; then
  warn "Python 3.10+ not found."
  if command -v brew >/dev/null 2>&1; then
    say "Installing Python via Homebrew…"; brew install python@3.12; PY="python3.12"
  else
    echo "Install Python 3.10+ (https://www.python.org/downloads/macos/ or 'brew install python') and re-run." >&2
    exit 1
  fi
fi
ok "Using $($PY --version)"

# 2) venv + harness ---------------------------------------------------------
if [ ! -d .venv ]; then say "Creating virtualenv (.venv)…"; "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
say "Installing the harness + local-LLM engine…"
python -m pip install --quiet --upgrade pip
EXTRAS="local"
[ "$WITH_MENUBAR" -eq 1 ] && EXTRAS="local,menubar"
python -m pip install --quiet -e ".[$EXTRAS]"
ok "Harness installed in .venv"
if [ "$WITH_MENUBAR" -eq 1 ]; then
  say "Building the menu-bar app…"; ./scripts/make-app.sh || warn "app build failed"
fi

# 3) Optional: Claude Code CLI ---------------------------------------------
if [ "$WITH_CLAUDE" -eq 1 ] && ! command -v claude >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    say "Installing the Claude Code CLI…"; npm install -g @anthropic-ai/claude-code || \
      warn "Claude CLI install failed — see https://docs.claude.com/claude-code"
  else
    warn "npm not found — install Node (brew install node) then: npm i -g @anthropic-ai/claude-code"
  fi
fi

# 4) Optional: Ollama + a coder model --------------------------------------
if [ "$WITH_OLLAMA" -eq 1 ]; then
  if ! command -v ollama >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then say "Installing Ollama…"; brew install ollama
    else warn "Install Ollama from https://ollama.com/download, then: ollama pull qwen2.5-coder"; fi
  fi
  if command -v ollama >/dev/null 2>&1; then
    say "Pulling qwen2.5-coder (good tool-calling coder for Apple Silicon)…"
    (ollama serve >/dev/null 2>&1 &) ; sleep 2
    ollama pull qwen2.5-coder || warn "pull failed — run 'ollama pull qwen2.5-coder' later"
  fi
fi

# 5) Report what's connected ------------------------------------------------
echo; python -m harness.doctor; echo
ok "Done. Start the web console with:"
echo "    ./scripts/start-mac.sh        (or:  source .venv/bin/activate && python -m harness.server)"
