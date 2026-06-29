#!/usr/bin/env bash
# Start the Lathe web console and open it in your browser.
#   ./scripts/start-mac.sh [--port 8765]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "No .venv found — run ./scripts/install-mac.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

PORT=8765
if [ "${1:-}" = "--port" ] && [ -n "${2:-}" ]; then PORT="$2"; fi

URL="http://127.0.0.1:${PORT}"
# Open the browser shortly after the server binds (macOS `open`).
( sleep 1.5; command -v open >/dev/null 2>&1 && open "$URL" || true ) &

echo "▸ Lathe console → ${URL}   (Ctrl-C to stop)"
exec python -m harness.server --host 127.0.0.1 --port "$PORT"
