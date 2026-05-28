"""Stdlib-only per-profile sliding-window rate limiter with 429 Retry-After support."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque

logger = logging.getLogger("hermes-a2a.rate_limiter")

_DEFAULT_RATE_PER_SECOND: int = 10
_DEFAULT_BURST: int = 20
_WINDOW_SECONDS: float = 1.0


def _env_int(name: str, default: int) -> int:
    """Read a positive int from env; fall back to ``default`` on parse error."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except (TypeError, ValueError):
        logger.warning("rate_limiter: %s=%r not parseable as int; using %d", name, raw, default)
        return default


class RateLimiter:
    """Per-profile sliding-window limiter; thread-safe; in-memory only."""

    def __init__(self, rate_per_second: int | None = None, burst: int | None = None) -> None:
        self.rate_per_second = rate_per_second if rate_per_second is not None else _env_int(
            "A2A_RATE_PER_SECOND", _DEFAULT_RATE_PER_SECOND
        )
        self.burst = burst if burst is not None else _env_int(
            "A2A_RATE_BURST", _DEFAULT_BURST
        )
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}
        self._last_seen: dict[str, float] = {}

    def check(self, profile_id: str) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)`` for ``profile_id``."""
        t = time.monotonic()
        cutoff = t - _WINDOW_SECONDS
        with self._lock:
            dq = self._windows.get(profile_id)
            if dq is None:
                dq = deque()
                self._windows[profile_id] = dq
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.burst:
                wait = dq[0] + _WINDOW_SECONDS - t
                retry_after = max(1.0, math.ceil(wait))
                return False, float(retry_after)
            dq.append(t)
            self._last_seen[profile_id] = t
            return True, 0.0

    def stats(self) -> dict[str, dict[str, float]]:
        """Snapshot ``{profile_id: {requests, last_seen}}`` for /health diagnostics."""
        with self._lock:
            return {
                pid: {"requests": len(dq), "last_seen": self._last_seen.get(pid, 0.0)}
                for pid, dq in self._windows.items()
            }


DEFAULT_LIMITER = RateLimiter()


__all__ = ["RateLimiter", "DEFAULT_LIMITER"]
