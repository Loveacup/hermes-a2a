#!/usr/bin/env python3
"""A2A HTTP/JSON-RPC Server for Hermes Agent."""

import json, logging, os, threading, uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

from agent_card import generate_agent_card
from task_handler import handle_task

logger = logging.getLogger("hermes-a2a.server")
HOST = os.environ.get("A2A_HOST", "127.0.0.1")
PORT = int(os.environ.get("A2A_PORT", "8650"))
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
MAX_TASKS = 1000
TASK_TTL_SECONDS = 3600  # 1 hour
_tasks: dict = {}

class A2AHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except (ValueError, TypeError):
            self._send_json({"error": "invalid Content-Length"}, 400)
            return None
        if length < 0 or length > 1_000_000:  # 1MB max
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

    def do_OPTIONS(self):
        self.send_response(204)
        for h, v in [("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"), ("Access-Control-Allow-Headers", "Content-Type")]:
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            return self._send_json({"status": "ok", "service": "hermes-a2a", "version": "0.1.0", "profile": os.environ.get("HERMES_PROFILE","default")})
        if self.path == "/a2a/.well-known/agent-card.json":
            return self._send_json(generate_agent_card(HERMES_HOME))
        if self.path.startswith("/a2a/tasks/") and "/stream" in self.path:
            tid = self.path.split("/")[3]
            return self._handle_sse(_tasks.get(tid, {"error": "not found"}))
        if self.path.startswith("/a2a/tasks/"):
            tid = self.path.split("/")[3]
            return self._send_json(_tasks.get(tid, {"error": "not found"}))
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path in ("/a2a/tasks", "/a2a/tasks/send"):
            body = self._read_body()
            if body is None:
                return  # _read_body already sent error response
            tid = body.get("id") or f"a2a-{uuid.uuid4().hex[:12]}"
            task = {"id": tid, "status": "working", "context_id": body.get("context_id"), "message": body.get("message"), "created_at": datetime.now(timezone.utc).isoformat(), "history": []}
            _prune_tasks()
            _tasks[tid] = task
            threading.Thread(target=_execute_task, args=(tid,), daemon=True).start()
            return self._send_json(task, 201)
        self._send_json({"error": "not found"}, 404)

    def _handle_sse(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.write("data: [DONE]\n\n".encode())

    def log_message(self, fmt, *args):
        logger.debug(f"{self.client_address[0]} - {fmt % args}")

def _execute_task(tid: str) -> None:
    """Run task_handler.handle_task in a background thread, update _tasks on completion."""
    task = _tasks.get(tid)
    if not task:
        return
    try:
        logger.info(f"[hermes-a2a] executing task {tid}")
        result = handle_task(task)
        _tasks[tid] = result
        logger.info(f"[hermes-a2a] task {tid} → {result.get('status')}")
    except Exception as e:
        _tasks[tid]["status"] = "failed"
        _tasks[tid]["error"] = str(e)
        logger.error(f"[hermes-a2a] task {tid} failed: {e}")

def _prune_tasks() -> None:
    """Remove tasks exceeding MAX_TASKS or TASK_TTL_SECONDS."""
    if len(_tasks) > MAX_TASKS:
        sorted_ids = sorted(_tasks.keys(), key=lambda tid: _tasks[tid].get("created_at", ""))
        for old_tid in sorted_ids[: len(_tasks) - MAX_TASKS]:
            del _tasks[old_tid]
    now = datetime.now(timezone.utc)
    expired = [tid for tid, t in _tasks.items()
               if (now - datetime.fromisoformat(t.get("created_at", "1970-01-01T00:00:00+00:00"))).total_seconds() > TASK_TTL_SECONDS]
    for tid in expired:
        del _tasks[tid]

def main():
    server = HTTPServer((HOST, PORT), A2AHandler)
    logger.info(f"[hermes-a2a] http://{HOST}:{PORT} | health | agent-card | tasks")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
