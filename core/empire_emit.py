"""EmpireThread emit hook — pre_tool_call → empire-thread.jsonl (TDD GREEN).

This module provides a Hermes plugin hook that writes tool-call events to
``empire-thread.jsonl``.  The daemon (``event_bridge/daemon.py``) consumes
this JSONL asynchronously and fans out to Obsidian + Supermemory.

Design:
- ``register_emit_hook(ctx)`` — called from ``plugin.py:register()``.
- ``_emit_handler(...)`` — the actual pre_tool_call callback.
- Sub-millisecond append-only write; no fsync (daemon tolerates crash gaps).
- All exceptions swallowed silently — the hook must never block the gateway.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _jsonl_path() -> Path | None:
    """Resolve ``empire-thread.jsonl`` for the current profile.

    HERMES_HOME is already profile-specific for non-default profiles
    (e.g. ``/Users/alexcai/.hermes/profiles/regent``), so we write
    directly under it.  Returns ``None`` if HERMES_HOME is not set.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    return Path(home) / "empire-thread.jsonl"


def _profile_from_home() -> str:
    """Derive profile name from HERMES_HOME path.

    Falls back to \"default\" if the path doesn't contain \"profiles/\".
    """
    home = os.environ.get("HERMES_HOME", "")
    parts = Path(home).parts
    try:
        idx = list(parts).index("profiles")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return "default"


def _emit_handler(
    tool_name: str,
    args: dict,
    task_id: str = "",
    run_id: str = "",
    **kwargs,
) -> None:
    """pre_tool_call hook callback — append event to JSONL.

    Returns ``None`` to signal "pass-through" (never blocks the tool call).
    All exceptions are caught silently — the hook is best-effort only.
    """
    path = _jsonl_path()
    if path is None:
        return None

    profile = os.environ.get("HERMES_PROFILE") or _profile_from_home()

    event = {
        "event_id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "event_type": "execute",
        "content": {
            "tool_name": tool_name,
            "args": _sanitize_args(args),
        },
        "run_id": run_id,
        "task_id": task_id or "",
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()  # push to OS buffer; no fsync (perf > durability)
    except Exception:
        # Best-effort: never block the gateway for an emit failure.
        pass

    return None


def _sanitize_args(args: dict) -> dict:
    """Shallow-copy and truncate large string values to keep events compact."""
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "…"
        elif isinstance(v, (dict, list)):
            # Skip nested structures (content, code blocks, etc.)
            out[k] = f"<{type(v).__name__}:{len(v)} items>"
        else:
            out[k] = v
    return out


def register_emit_hook(ctx) -> None:
    """Register the emit hook on ``ctx`` (Hermes PluginContext).

    Called from ``plugin.py:register()`` during gateway startup.
    """
    ctx.register_hook("pre_tool_call", _emit_handler)
