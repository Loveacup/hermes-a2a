"""Audit-hook score-only path for A2A task completion (审计全闭环).

This module survived the v0.15.x audit (2026-05-29) as a focused score+alert
closure. The original four-defense gate (AuditGate / DEFAULT_GATE /
audit_depth / next_depth_headers) was amputated because Hermes v0.15.0 ships
equivalent worker protection natively (respawn guard #28455, claim TTL
#28392, stale-detection #28452, fingerprint crash errors #28380,
max_in_progress #28420).

What stays here
---------------
``score_task(task)``  — 4-dimensional quality score, writes ``task['audit_score']``.
``maybe_alert(task)`` — telegram alert + reviewer kanban card when score < threshold.

Both are called from ``server.py`` after every task completion to keep the
"审计全闭环" wired up (commit 08305ab, roadmap §11.1 ✅).

See /Users/alexcai/.hermes/tmp/hermes-v015-audit-report.md §2.4 for the
amputation rationale.
"""

from __future__ import annotations

import json
import os
import pwd
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Task scoring (score-only mode)
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


# ---------------------------------------------------------------------------
# Low-score alerting (审计全闭环)
# ---------------------------------------------------------------------------

ALERT_THRESHOLD = 0.4          # overall below this → alert
ALERT_COOLDOWN_S = 300          # max 1 per 5 min
ALERT_ASSIGNEE = "auditor"

_alerted_tasks: set[str] = set()
_last_alert_ts: float = 0.0


def maybe_alert(task: dict) -> dict | None:
    """Check score; alert + Kanban card if below threshold."""
    score = (task.get("audit_score") or {}).get("overall")
    if score is None or score >= ALERT_THRESHOLD:
        return None
    tid = task.get("id", "")
    if tid in _alerted_tasks:
        return None
    import time as _t
    global _last_alert_ts
    now = _t.time()
    if now - _last_alert_ts < ALERT_COOLDOWN_S:
        return None
    _alerted_tasks.add(tid)
    _last_alert_ts = now
    dims = task.get("audit_score", {})
    msg = (
        f"⚠️ 审计告警\nTask: {tid}\nScore: {score} (threshold={ALERT_THRESHOLD})\n"
        f"exec={dims.get('execution')} acc={dims.get('accuracy')} "
        f"comp={dims.get('compliance')}\n"
        f"status={task.get('status')} semantic={task.get('semantic_status')}"
    )
    import subprocess as _sp
    _sp.run(["hermes","-p","regent","send","-t","telegram:7931997806",msg],
            timeout=10, capture_output=True)
    _sp.run(["hermes","-p","regent","kanban","create",
             f"audit-review-{tid[:8]}","--assignee",ALERT_ASSIGNEE,
             "--body", f"审计低分复审 task={tid} score={score}"],
            timeout=10, capture_output=True)
    return {"task_id": tid, "score": score, "alerted": True}


# ---------------------------------------------------------------------------
# EmpireThread event emission (P0-3 — wires A2A task completion → event bridge)
# ---------------------------------------------------------------------------


def _hermes_root() -> Path:
    """Resolve shared ~/.hermes root even when HOME is per-profile hijacked.

    Mirrors core.paths.hermes_root to avoid cross-package import cost.
    """
    override = os.environ.get("HERMES_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    hh = os.environ.get("HERMES_HOME", "").strip()
    if hh:
        p = Path(hh).expanduser()
        parts = p.parts
        if ".hermes" in parts:
            idx = parts.index(".hermes")
            return Path(*parts[: idx + 1])
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"


def emit_empire_event(task: dict) -> dict:
    """Append a task_completed event to ``{profile_home}/empire-thread.jsonl``.

    Atomic write: existing bytes + new line → tmp file → ``os.replace`` to
    target so the daemon never reads a half-written line. Caller (server.py)
    wraps in try/except and logs failures via ``logger.exception``.

    Returns the emitted event dict (for tests / logging).
    """
    profile = os.environ.get("HERMES_PROFILE", "default")
    target = _hermes_root() / "profiles" / profile / "empire-thread.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)

    artifact = task.get("artifact") if isinstance(task.get("artifact"), dict) else {}
    audit_score = artifact.get("audit_score") or task.get("audit_score")

    event = {
        "event_id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "event": "task_completed",
        "data": {
            "status": task.get("status"),
            "semantic_status": task.get("semantic_status"),
            "completion_reason": task.get("completion_reason"),
            "duration_s": artifact.get("actual_duration_s"),
            "audit_score": audit_score,
        },
        "task_id": task.get("id", "") or "",
        "source": "audit_hook",
    }
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    existing = target.read_bytes() if target.exists() else b""
    tmp = target.with_name(
        f"{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_bytes(existing + line)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return event


__all__ = [
    "score_task",
    "maybe_alert",
    "emit_empire_event",
    "ALERT_THRESHOLD",
    "ALERT_COOLDOWN_S",
    "ALERT_ASSIGNEE",
]
