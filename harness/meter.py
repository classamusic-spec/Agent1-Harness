"""Live token-throughput meter for local builds.

Local models on a Mac run at very different speeds (a 7B coder might do 80 tok/s,
a quantized GLM 20). Seeing tokens/sec stream live tells you at a glance whether
the model is healthy or thrashing swap — and makes a slow local build feel alive
instead of frozen. Tokens are estimated from streamed text (~4 chars/token) since
OpenAI-compatible servers only report exact usage at end-of-turn; the estimate is
reconciled with the real count when it arrives. Pure + stdlib; fully unit-tested.
"""

from __future__ import annotations

import time


def approx_tokens(text: str) -> int:
    """Rough token count for streamed text. ~4 chars/token is the usual heuristic."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))


class TokenMeter:
    """Accumulate streamed tokens over wall-clock and report a throttled rate."""

    def __init__(self, *, now=time.monotonic, every: float = 1.5):
        self._now = now
        self.every = every
        self.tokens = 0
        self.t0: float | None = None
        self._last_emit: float | None = None

    def start(self) -> "TokenMeter":
        self.t0 = self._now()
        self._last_emit = self.t0
        return self

    def add(self, n: int) -> None:
        if self.t0 is None:
            self.start()
        self.tokens += max(0, int(n))

    def add_text(self, text: str) -> None:
        self.add(approx_tokens(text))

    def set_tokens(self, n: int) -> None:
        """Reconcile with an exact usage count from the server (only ever raises it)."""
        if n and int(n) > self.tokens:
            self.tokens = int(n)

    def elapsed(self) -> float:
        return 0.0 if self.t0 is None else max(1e-6, self._now() - self.t0)

    def rate(self) -> float:
        """Tokens per second so far (0 before the first token)."""
        return 0.0 if self.t0 is None else self.tokens / self.elapsed()

    def due(self) -> bool:
        """True at most once per `every` seconds — for throttling live output."""
        if self.t0 is None:
            return False
        t = self._now()
        if self._last_emit is None or (t - self._last_emit) >= self.every:
            self._last_emit = t
            return True
        return False

    def line(self) -> str:
        return f"⚡ {self.rate():.0f} tok/s · {self.tokens:,} tok · {self.elapsed():.0f}s"
