#!/usr/bin/env python3
"""A2A HTTP/JSON-RPC Server for Hermes Agent.

Thread-safe via ThreadingHTTPServer + SQLite-backed task storage (storage.py).
Bearer-token auth + CORS allowlist (auth.py). Identity prefix loaded out of
core via identity.py. Task execution stays inline in this module — the
handler thread enqueues a worker thread per task.
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from agent_card import generate_agent_card
from task_handler import handle_task
from storage import TaskStore
from auth import (
    check_auth,
    cors_headers,
    is_public_path,
    load_or_create_token,
)
from rate_limiter import DEFAULT_LIMITER
from audit_hook import score_task, maybe_alert

logger = logging.getLogger("hermes-a2a.server")

HOST = os.environ.get("A2A_HOST", "127.0.0.1")
PORT = int(os.environ.get("A2A_PORT", "8650"))
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
MAX_TASKS = int(os.environ.get("A2A_MAX_TASKS", "1000"))
TASK_TTL_SECONDS = int(os.environ.get("A2A_TASK_TTL", "3600"))  # 1 hour
TASK_TIMEOUT_DEFAULT = int(os.environ.get("A2A_TASK_TIMEOUT", "30"))
TASK_TIMEOUT_CHAIN = int(os.environ.get("A2A_TASK_TIMEOUT_CHAIN", "120"))
MAX_BODY_BYTES = 1_000_000  # 1 MB

# SQLite path per profile so concurrent profile processes don't share state.
_DB_PATH = Path(HERMES_HOME) / "data" / f"a2a-{os.environ.get('HERMES_PROFILE','default')}.db"

_store = TaskStore(_DB_PATH, max_tasks=MAX_TASKS, ttl_seconds=TASK_TTL_SECONDS)
_token = load_or_create_token(HERMES_HOME)
_exec_lock = threading.Lock()  # guards _store.save+spawn against double-execute races

# Observability counters (P3 metrics endpoint). Process-local, reset on restart.
_start_time = time.monotonic()
_metrics_lock = threading.Lock()
_metrics = {
    "requests_total": 0,
    "rate_limited": 0,
    "tasks_completed": 0,
    "tasks_failed": 0,
    "tasks_working": 0,
}


def _inc(key: str, delta: int = 1) -> None:
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + delta


class A2AHandler(BaseHTTPRequestHandler):
    # ── Response helpers ────────────────────────────────────────────
    def _set_cors(self) -> None:
        origin = self.headers.get("Origin")
        for k, v in cors_headers(origin).items():
            self.send_header(k, v)

    def _send_json(self, data, status=200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cors()
        self.end_headers()
        self.wfile.write(body)

    def _auth_or_reject(self) -> bool:
        ok, reason = check_auth(self.headers, self.path)
        if ok:
            return True
        status = 401 if "Authorization" in reason or "token" in reason else 403
        self._send_json({"error": "unauthorized", "reason": reason}, status)
        return False

    def _read_body(self):
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except (ValueError, TypeError):
            self._send_json({"error": "invalid Content-Length"}, 400)
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json({"error": "Content-Length out of range"}, 413)
            return None
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            self._send_json({"error": f"invalid JSON: {e}"}, 400)
            return None

    # ── CORS preflight ──────────────────────────────────────────────
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    # ── GET routes ──────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        # Public endpoints — no auth.
        if path == "/health":
            return self._send_json({
                "status": "ok",
                "service": "hermes-a2a",
                "version": "0.2.0",
                "profile": os.environ.get("HERMES_PROFILE", "default"),
            })
        if path == "/a2a/.well-known/agent-card.json":
            return self._send_json(generate_agent_card(HERMES_HOME))

        # Protected endpoints.
        if not self._auth_or_reject():
            return

        # GET /a2a/metrics — observability counters (P3)
        if path == "/a2a/metrics":
            with _metrics_lock:
                snapshot = dict(_metrics)
            snapshot["profile"] = os.environ.get("HERMES_PROFILE", "default")
            snapshot["uptime_s"] = int(time.monotonic() - _start_time)
            try:
                snapshot["a2a_tasks_stored"] = _store.count()
            except Exception:
                snapshot["a2a_tasks_stored"] = -1
            return self._send_json(snapshot)

        # GET /a2a/tasks — list tasks (P0-8 Step 1)
        if path == "/a2a/tasks":
            limit, status = self._parse_list_query()
            tasks = _store.list(limit=limit, status=status)
            return self._send_json({
                "tasks": tasks,
                "count": len(tasks),
                "limit": limit,
                "filter_status": status,
            })

        # GET /a2a/tasks/{id}/stream — SSE
        if path.startswith("/a2a/tasks/") and path.endswith("/stream"):
            tid = path.split("/")[3]
            return self._handle_sse(_store.get(tid) or {"error": "not found", "id": tid})

        # GET /a2a/tasks/{id}
        if path.startswith("/a2a/tasks/"):
            tid = path.split("/")[3]
            task = _store.get(tid)
            if task is None:
                return self._send_json({"error": "not found", "id": tid}, 404)
            return self._send_json(task)

        self._send_json({"error": "not found"}, 404)

    def _parse_list_query(self) -> tuple[int, str | None]:
        from urllib.parse import urlsplit, parse_qs

        qs = parse_qs(urlsplit(self.path).query)
        try:
            limit = int(qs.get("limit", ["100"])[0])
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 1000))
        status_vals = qs.get("status", [None])
        status = status_vals[0] if status_vals and status_vals[0] else None
        return limit, status

    # ── POST routes ─────────────────────────────────────────────────
    def do_POST(self) -> None:
        _inc("requests_total")
        profile_id = os.environ.get("HERMES_PROFILE", "default")
        allowed, retry_after = DEFAULT_LIMITER.check(profile_id)
        if not allowed:
            _inc("rate_limited")
            logger.info(
                "event=rate_limited profile=%s retry_after=%d",
                profile_id, int(retry_after) or 1,
            )
            body = json.dumps(
                {"error": "rate_limited", "retry_after": retry_after},
                ensure_ascii=False,
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", str(int(retry_after) or 1))
            self._set_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._auth_or_reject():
            return

        if self.path in ("/a2a/tasks", "/a2a/tasks/send"):
            body = self._read_body()
            if body is None:
                return
            # Server-generated id always wins; never trust client to set it.
            tid = f"a2a-{uuid.uuid4().hex}"  # full 32-char hex (P0-10)
            task = {
                "id": tid,
                "status": "working",
                "context_id": body.get("context_id"),
                "message": body.get("message"),
                "timeout_s": _resolve_timeout(body.get("message")),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "history": [],
            }
            with _exec_lock:
                _store.save(task)
                _store.prune()
                _inc("tasks_working")
                threading.Thread(
                    target=_execute_task, args=(tid,), daemon=True,
                ).start()
            return self._send_json(task, 201)

        self._send_json({"error": "not found"}, 404)

    # ── SSE ─────────────────────────────────────────────────────────
    def _handle_sse(self, data) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._set_cors()
        self.end_headers()
        self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, fmt, *args) -> None:
        logger.debug("%s - %s", self.client_address[0], fmt % args)


def _resolve_timeout(message) -> int:
    if isinstance(message, dict):
        explicit = message.get("timeout")
        if isinstance(explicit, (int, float)) and explicit > 0:
            return min(int(explicit), 3600)
        if message.get("chain") or message.get("chain_call"):
            return TASK_TIMEOUT_CHAIN
    return TASK_TIMEOUT_DEFAULT


def _execute_task(tid: str) -> None:
    """Run task_handler.handle_task in a background thread, persist result."""
    task = _store.get(tid)
    if not task:
        _inc("tasks_working", -1)
        return
    profile = os.environ.get("HERMES_PROFILE", "default")
    t_start = time.monotonic()
    try:
        logger.info("event=task_start task_id=%s profile=%s", tid, profile)
        result = handle_task(task)
        if not isinstance(result.get("artifact"), dict):
            result["artifact"] = {}
        result["artifact"]["timeout_s"] = task.get("timeout_s", TASK_TIMEOUT_DEFAULT)
        dur_ms = int((time.monotonic() - t_start) * 1000)
        result["artifact"]["actual_duration_s"] = round(dur_ms / 1000, 3)
        try:
            result["artifact"]["audit_score"] = score_task(result)
        except Exception:
            logger.exception(
                "event=score_task_error task_id=%s profile=%s", tid, profile,
            )
        # Low-score alert (best-effort, never blocks task completion)
        try:
            maybe_alert(result)
        except Exception:
            logger.exception(
                "event=alert_error task_id=%s profile=%s", tid, profile,
            )
        _store.save(result)
        status = result.get("status", "?")
        if status == "completed":
            _inc("tasks_completed")
        else:
            _inc("tasks_failed")
        _inc("tasks_working", -1)
        logger.info(
            "event=task_complete task_id=%s profile=%s status=%s semantic=%s reason=%s dur_ms=%d",
            tid, profile, status,
            result.get("semantic_status", "?"),
            result.get("completion_reason", "?"),
            dur_ms,
        )
    except Exception as e:  # pragma: no cover — defensive
        task["status"] = "failed"
        task["error"] = str(e)
        _store.save(task)
        _inc("tasks_failed")
        _inc("tasks_working", -1)
        logger.exception(
            "event=task_failed task_id=%s profile=%s error=%s", tid, profile, e,
        )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), A2AHandler)
    server.daemon_threads = True
    logger.info(
        "event=server_start host=%s port=%d profile=%s db=%s",
        HOST, PORT, os.environ.get("HERMES_PROFILE", "default"), _DB_PATH,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _store.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
