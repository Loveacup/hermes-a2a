#!/usr/bin/env python3
"""A2A HTTP/JSON-RPC Server for Hermes Agent."""

import json, logging, os, uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

from agent_card import generate_agent_card

logger = logging.getLogger("hermes-a2a.server")
HOST = os.environ.get("A2A_HOST", "127.0.0.1")
PORT = int(os.environ.get("A2A_PORT", "8650"))
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
_tasks: dict = {}

class A2AHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

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
            tid = body.get("id") or f"a2a-{uuid.uuid4().hex[:12]}"
            task = {"id": tid, "status": "working", "context_id": body.get("context_id"), "message": body.get("message"), "created_at": datetime.now(timezone.utc).isoformat(), "history": []}
            _tasks[tid] = task
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
