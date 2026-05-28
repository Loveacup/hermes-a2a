"""Audit-hook gate for A2A reviewer fan-out (P0-4).

Skeleton module that protects the A2A server from audit-loop avalanches.
Four defensive lines, evaluated in order:

  1. **Skip-list profiles** — profile names like ``reviewer`` / ``auditor``
     never trigger a new audit fan-out on themselves (would loop forever).
  2. **Depth header** — inbound requests carry ``X-A2A-Audit-Depth``;
     each hop increments it.  When ``depth >= MAX_DEPTH`` the gate
     refuses to schedule another audit.
  3. **Bounded semaphore** — at most ``MAX_CONCURRENT`` audit hooks
     in flight per process.  ``acquire(blocking=False)`` returns False
     when the pool is saturated; callers fall back to fire-and-forget
     or drop.
  4. **Sliding-window rate limit** — at most ``RATE_PER_MINUTE`` audit
     hooks may be triggered in any 60-second window.  Implemented with
     a deque of timestamps under a lock.

The gate is intentionally pure stdlib (no third-party deps) and stateless
across process restarts.  Counters are in-memory; if you need cross-process
limits, externalise via Redis later.

Public API
----------
``DEFAULT_GATE`` — module-level shared instance, sane defaults.
``AuditGate``    — instantiate your own for tests/tuning.
``audit_depth(headers)`` — read & validate the depth header.
``next_depth_headers(headers)`` — build outbound headers (depth+1).

Status
------
SKELETON — not yet wired into server.py.  When integrating, call
``gate.check(profile=..., headers=req.headers)`` before scheduling an
audit task, then enter ``gate.acquire()`` for the actual execution.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator, Mapping

logger = logging.getLogger("hermes-a2a.audit_hook")

# ---------------------------------------------------------------------------
# Constants & env-tunable defaults
# ---------------------------------------------------------------------------

DEPTH_HEADER: str = "X-A2A-Audit-Depth"

# Profiles that should never trigger a downstream audit hook.
# Configurable via env A2A_AUDIT_SKIP_PROFILES="reviewer,auditor,critic".
_DEFAULT_SKIP_PROFILES: frozenset[str] = frozenset({"reviewer", "auditor", "critic"})

_DEFAULT_MAX_DEPTH: int = 2          # 0 → 1 → 2; refuse at 2.
_DEFAULT_MAX_CONCURRENT: int = 8     # bounded semaphore
_DEFAULT_RATE_PER_MINUTE: int = 60   # tokens per 60s rolling window
_WINDOW_SECONDS: float = 60.0


def _env_int(name: str, default: int) -> int:
    """Read a positive int from env; fall back to ``default`` on parse error."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except (TypeError, ValueError):
        logger.warning("audit_hook: %s=%r not parseable as int; using %d", name, raw, default)
        return default


def _env_profiles() -> frozenset[str]:
    raw = os.environ.get("A2A_AUDIT_SKIP_PROFILES", "").strip()
    if not raw:
        return _DEFAULT_SKIP_PROFILES
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return frozenset(parts) if parts else _DEFAULT_SKIP_PROFILES


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class AuditGate:
    """Composite gate enforcing the four defensive lines.

    A single instance is process-wide.  All counters are in-memory; restart
    resets them.  Thread-safe (locks + bounded semaphore).
    """

    def __init__(
        self,
        max_depth: int | None = None,
        max_concurrent: int | None = None,
        rate_per_minute: int | None = None,
        skip_profiles: frozenset[str] | None = None,
    ) -> None:
        self.max_depth = max_depth if max_depth is not None else _env_int(
            "A2A_AUDIT_MAX_DEPTH", _DEFAULT_MAX_DEPTH
        )
        self.max_concurrent = max_concurrent if max_concurrent is not None else _env_int(
            "A2A_AUDIT_MAX_CONCURRENT", _DEFAULT_MAX_CONCURRENT
        )
        self.rate_per_minute = rate_per_minute if rate_per_minute is not None else _env_int(
            "A2A_AUDIT_RATE_PER_MINUTE", _DEFAULT_RATE_PER_MINUTE
        )
        self.skip_profiles = skip_profiles if skip_profiles is not None else _env_profiles()

        self._sem = threading.BoundedSemaphore(self.max_concurrent)
        self._win_lock = threading.Lock()
        self._timestamps: deque[float] = deque()

        # Observability — useful in /health diagnostics.
        self._stats_lock = threading.Lock()
        self._stats = {
            "skipped_profile": 0,
            "skipped_depth": 0,
            "skipped_semaphore": 0,
            "skipped_rate": 0,
            "scheduled": 0,
        }

    # ── Pre-flight check ──────────────────────────────────────────────
    def check(
        self,
        *,
        profile: str | None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bool, str]:
        """Return ``(ok, reason)`` without consuming any slot.

        - ``ok=True``  → caller may proceed to ``acquire()``.
        - ``ok=False`` → ``reason`` is a short tag (``profile`` / ``depth``
                         / ``rate``).  Caller should log and drop.

        ``concurrency`` failures are only observable inside ``acquire()``
        because semaphore state can change between check and acquire.
        """
        if profile and profile.lower() in self.skip_profiles:
            self._bump("skipped_profile")
            return False, "profile"

        depth = audit_depth(headers or {})
        if depth >= self.max_depth:
            self._bump("skipped_depth")
            return False, "depth"

        if not self._rate_ok(consume=False):
            self._bump("skipped_rate")
            return False, "rate"

        return True, ""

    # ── Slot acquisition (context manager) ───────────────────────────
    @contextmanager
    def acquire(self, *, consume_rate: bool = True) -> Iterator[bool]:
        """Acquire a concurrency slot.  Use as a context manager.

        Yields ``True`` on success (slot held until exit) or ``False`` when
        the pool is saturated.  Caller should always check the yielded
        value.  When ``consume_rate`` is True (default), the rate-limit
        window also consumes a token.

        Example::

            with gate.acquire() as granted:
                if not granted:
                    return  # dropped by gate
                spawn_audit_task(...)
        """
        if not self._sem.acquire(blocking=False):
            self._bump("skipped_semaphore")
            yield False
            return
        try:
            if consume_rate and not self._rate_ok(consume=True):
                self._bump("skipped_rate")
                yield False
                return
            self._bump("scheduled")
            yield True
        finally:
            self._sem.release()

    # ── Helpers ───────────────────────────────────────────────────────
    def _rate_ok(self, *, consume: bool) -> bool:
        now = time.monotonic()
        cutoff = now - _WINDOW_SECONDS
        with self._win_lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.rate_per_minute:
                return False
            if consume:
                self._timestamps.append(now)
            return True

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def stats(self) -> dict[str, int]:
        """Return a snapshot of counters (skipped_* / scheduled)."""
        with self._stats_lock:
            return dict(self._stats)


# ---------------------------------------------------------------------------
# Depth header helpers (usable without instantiating a gate)
# ---------------------------------------------------------------------------

def audit_depth(headers: Mapping[str, str]) -> int:
    """Read ``X-A2A-Audit-Depth`` from incoming headers; default 0.

    Malformed / negative values are clamped to 0 (fail-open at the gate;
    downstream depth checks still bound recursion).
    """
    try:
        raw = headers.get(DEPTH_HEADER) or headers.get(DEPTH_HEADER.lower()) or "0"
    except AttributeError:
        return 0
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def next_depth_headers(headers: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return outbound headers with depth incremented by 1.

    Use when this process is *originating* an audit fan-out — wrap the
    current request's depth and pass it forward.  Caller merges this dict
    into its own ``headers={...}``.
    """
    current = audit_depth(headers or {})
    return {DEPTH_HEADER: str(current + 1)}


# ---------------------------------------------------------------------------
# Process-wide default instance
# ---------------------------------------------------------------------------

DEFAULT_GATE = AuditGate()


# ---------------------------------------------------------------------------
# Task scoring (score-only mode, P0-4 follow-up)
# ---------------------------------------------------------------------------

def score_task(task: dict) -> dict:
    """4-dimensional task quality score (0.0-1.0 each); writes task['audit_score'].

    Dimensions:
        execution   — based on status (completed=1.0, failed=0.0, working=0.5)
        accuracy    — based on semantic_status (succeeded=1.0, degraded=0.6, failed=0.0)
        compliance  — heuristic on response length & artifact presence
        retry_eff   — placeholder 1.0 (no retry tracking yet); reduces when error present

    Score-only mode: never alerts, retries, or escalates. Just observes.
    """
    artifact = task.get("artifact") if isinstance(task.get("artifact"), dict) else {}

    status = task.get("status", "")
    execution = {"completed": 1.0, "failed": 0.0}.get(status, 0.5)

    sem = task.get("semantic_status", "")
    accuracy = {"succeeded": 1.0, "degraded": 0.6, "failed": 0.0}.get(sem, 0.5)

    response = artifact.get("response", "") or artifact.get("fallback_text", "")
    if isinstance(response, str) and len(response.strip()) >= 10:
        compliance = 1.0 if artifact else 0.7
    else:
        compliance = 0.3

    retry_eff = 0.5 if task.get("error") else 1.0

    overall = round((execution + accuracy + compliance + retry_eff) / 4, 3)

    task["audit_score"] = {
        "overall": overall,
        "execution": round(execution, 3),
        "accuracy": round(accuracy, 3),
        "compliance": round(compliance, 3),
        "retry_eff": round(retry_eff, 3),
        "mode": "score_only",
    }
    return task["audit_score"]


__all__ = [
    "AuditGate",
    "DEFAULT_GATE",
    "DEPTH_HEADER",
    "audit_depth",
    "next_depth_headers",
    "score_task",
]
