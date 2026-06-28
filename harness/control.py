"""Cooperative run controls: pause / cancel a build mid-flight.

The build loop checks `requested()` at each round boundary and stops gracefully
when pause or cancel has been requested. Because the workspace and a checkpoint
persist, a paused build can be continued later via resume — pause is "stop now,
I'll come back"; cancel is "stop, I'm done". Both are thread-safe so the web
console can flip them from a request handler while the build runs in its thread.
"""

from __future__ import annotations

import threading


class BuildControl:
    def __init__(self):
        self._req: str | None = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._set("cancelled")

    def pause(self) -> None:
        self._set("paused")

    def _set(self, reason: str) -> None:
        with self._lock:
            if self._req is None:  # first request wins
                self._req = reason

    def requested(self) -> str | None:
        with self._lock:
            return self._req
