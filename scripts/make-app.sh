#!/usr/bin/env bash
# Build "Agent1-Harness.app" — a menu-bar app bundle you can drag to /Applications.
# Double-clicking it puts a ✦ in the menu bar (start/stop/open the console).
# Requires the venv + the menubar extra:  ./scripts/install-mac.sh --with-menubar
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP="$ROOT/Agent1-Harness.app"

if [ ! -d .venv ]; then
  echo "No .venv — run ./scripts/install-mac.sh --with-menubar first." >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Agent1-Harness</string>
  <key>CFBundleDisplayName</key><string>Agent1-Harness</string>
  <key>CFBundleIdentifier</key><string>com.agent1.harness</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>run</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/run" <<RUN
#!/usr/bin/env bash
cd "$ROOT" || exit 1
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
exec python "$ROOT/scripts/menubar.py"
RUN
chmod +x "$APP/Contents/MacOS/run"

echo "✓ Built $APP"
echo "  Double-click it, or drag it to /Applications. Look for ✦ in the menu bar."
