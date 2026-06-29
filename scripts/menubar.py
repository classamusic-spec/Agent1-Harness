"""macOS menu-bar app for Agent1-Harness.

A tiny ✦ menu in the macOS menu bar to start/stop the web console and open it —
no Terminal needed. Built on `rumps` (a small menu-bar lib).

  pip install -e ".[menubar]"        # or: ./scripts/install-mac.sh --with-menubar
  python scripts/menubar.py          # ✦ appears in the menu bar

Or build a double-clickable app:  ./scripts/make-app.sh  →  Agent1-Harness.app
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request

PORT = int(os.environ.get("HARNESS_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _server_up() -> bool:
    try:
        urllib.request.urlopen(URL + "/api/health", timeout=0.6)
        return True
    except Exception:
        return False


def main() -> int:
    try:
        import rumps
    except ImportError:
        print("The menu-bar app needs `rumps`. Install it with:\n"
              "  pip install -e \".[menubar]\"   (or ./scripts/install-mac.sh --with-menubar)",
              file=sys.stderr)
        return 1

    class HarnessBar(rumps.App):
        def __init__(self):
            super().__init__("✦", quit_button=None)
            self.proc: subprocess.Popen | None = None
            self.menu = ["Open console", "Start server", "Stop server", None,
                         "Run doctor", None, "Quit"]
            self._sync()
            rumps.Timer(self._tick, 3).start()

        # --- helpers ---------------------------------------------------------
        def _running(self) -> bool:
            return (self.proc is not None and self.proc.poll() is None) or _server_up()

        def _sync(self):
            up = self._running()
            self.title = "✦" if up else "✦"
            self.menu["Start server"].set_callback(None if up else self.start)
            self.menu["Stop server"].set_callback(self.stop if self.proc else None)
            self.menu["Open console"].set_callback(self.open if up else None)

        def _tick(self, _):
            self._sync()

        # --- actions ---------------------------------------------------------
        def start(self, _):
            if self._running():
                return
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "harness.server", "--host", "127.0.0.1",
                 "--port", str(PORT)],
                cwd=ROOT, start_new_session=True)
            rumps.Timer(lambda t: (self.open(None), t.stop()), 1.5).start()
            self._sync()

        def stop(self, _):
            if self.proc and self.proc.poll() is None:
                import signal
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                except Exception:
                    self.proc.terminate()
            self.proc = None
            self._sync()

        def open(self, _):
            subprocess.run(["open", URL], check=False)

        @rumps.clicked("Run doctor")
        def doctor(self, _):
            out = subprocess.run([sys.executable, "-m", "harness.doctor"],
                                 cwd=ROOT, capture_output=True, text=True).stdout
            rumps.alert("Engine check", out or "(no output)")

        @rumps.clicked("Quit")
        def quit_app(self, _):
            self.stop(None)
            rumps.quit_application()

    HarnessBar().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
