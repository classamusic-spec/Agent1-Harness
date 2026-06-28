"""Capture a screenshot of a built app with a headless Chromium, if one is available.

Used by the visual-diff refinement loop to render the workspace's index.html and
compare it against a reference image. Degrades gracefully (returns None) when no
browser is installed — the loop then simply skips.
"""

from __future__ import annotations

import glob
import os
import subprocess
import tempfile


def find_chrome() -> str | None:
    """Locate a Chromium/Chrome binary. $HARNESS_CHROME wins; then PATH; then bundles."""
    env = os.environ.get("HARNESS_CHROME")
    if env and os.path.isfile(env):
        return env
    from shutil import which
    for name in ("chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable", "chrome"):
        p = which(name)
        if p:
            return p
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                "/opt/pw-browsers/chromium/chrome-linux/chrome",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def capture(html_path: str, *, width: int = 1280, height: int = 900,
            scale: float = 1.0, timeout: int = 60) -> bytes | None:
    """Render a local HTML file and return PNG bytes, or None if not possible."""
    chrome = find_chrome()
    if not chrome or not os.path.isfile(html_path):
        return None
    url = "file://" + os.path.abspath(html_path)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "shot.png")
        cmd = [
            chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
            f"--user-data-dir={os.path.join(tmp, 'prof')}",
            f"--window-size={width},{height}",
            f"--force-device-scale-factor={scale}",
            "--virtual-time-budget=2800",
            f"--screenshot={out}", url,
        ]
        try:
            subprocess.run(cmd, timeout=timeout, capture_output=True)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if os.path.isfile(out):
            with open(out, "rb") as fh:
                return fh.read()
    return None
