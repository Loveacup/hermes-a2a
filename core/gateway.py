"""Unified A2A Gateway: reverse-proxies 16 profile ports under registry:8928 (stdlib only)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger("hermes-a2a.gateway")

_PORT_RE = re.compile(r"^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`")
_FORWARD_TIMEOUT = 5.0
_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
})

# Observability counters (P3). Process-local, reset on restart.
_start_time = time.monotonic()
_metrics_lock = threading.Lock()
_metrics = {
    "proxied_requests": 0,
    "backend_errors": 0,
}


def _inc(key: str, delta: int = 1) -> None:
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + delta


def load_port_map(path: str) -> dict[str, int]:
    """Parse port-map.md into ``{profile: port}``."""
    routes: dict[str, int] = {}
    with open(path) as f:
        for line in f:
            m = _PORT_RE.match(line)
            if m:
                routes[m.group(1)] = int(m.group(2))
    return routes


class GatewayHandler(BaseHTTPRequestHandler):
    routes: dict[str, int] = {}

    def log_message(self, format: str, *args) -> None:
        logger.debug("gateway-http: " + format, *args)

    def _send_json(self, status: int, body: dict, extra_headers: dict | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _parse_a2a_path(self) -> tuple[str, str] | None:
        parts = self.path.split("/", 3)
        if len(parts) < 4 or parts[1] != "a2a":
            return None
        return parts[2], "/" + parts[3]

    def _build_headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in self.headers.items():
            if k.lower() in _HOP_HEADERS:
                continue
            out[k] = v
        return out

    def _forward(self, target_url: str, method: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(target_url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_FORWARD_TIMEOUT) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read() or b""
        except (urllib.error.URLError, OSError) as e:
            return 502, {}, json.dumps({"error": "upstream", "detail": str(e)}).encode()

    def _proxy(self, method: str) -> None:
        t0 = time.monotonic()
        if self.path == "/health" and method == "GET":
            self._send_json(200, {"status": "ok", "service": "hermes-a2a-gateway", "profiles": len(self.routes)})
            return
        if self.path == "/gateway/metrics" and method == "GET":
            with _metrics_lock:
                snapshot = dict(_metrics)
            snapshot["uptime_s"] = int(time.monotonic() - _start_time)
            snapshot["profiles"] = len(self.routes)
            self._send_json(200, snapshot)
            return
        parsed = self._parse_a2a_path()
        if parsed is None:
            self._send_json(404, {"error": "not_found", "path": self.path})
            return
        profile, rest = parsed
        port = self.routes.get(profile)
        if port is None:
            self._send_json(404, {"error": "unknown_profile", "profile": profile})
            return
        allowed_get = rest == "/.well-known/agent-card.json"
        allowed_post = rest in ("/tasks", "/tasks/send")
        if method == "GET" and not allowed_get:
            self._send_json(404, {"error": "not_found", "path": self.path})
            return
        if method == "POST" and not allowed_post:
            self._send_json(404, {"error": "not_found", "path": self.path})
            return
        body: bytes | None = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
        target = f"http://127.0.0.1:{port}/a2a{rest}"
        headers = self._build_headers()
        status, resp_headers, resp_body = self._forward(target, method, body, headers)
        _inc("proxied_requests")
        if status >= 500:
            _inc("backend_errors")
        self.send_response(status)
        ctype = resp_headers.get("Content-Type") or resp_headers.get("content-type") or "application/json"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)
        dur_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "event=proxy profile=%s path=%s method=%s status=%d dur_ms=%d",
            profile, self.path, method, status, dur_ms,
        )

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")


def main() -> None:
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8928"))
    default_map = os.path.expanduser("~/code/hermes-a2a/s6m-config/port-map.md")
    map_path = os.environ.get("PORT_MAP_PATH", default_map)
    routes = load_port_map(map_path)
    GatewayHandler.routes = routes
    logger.info("event=routes_loaded count=%d source=%s", len(routes), map_path)
    for profile in sorted(routes):
        logger.info("event=route profile=%s target=127.0.0.1:%d", profile, routes[profile])
    server = ThreadingHTTPServer((host, port), GatewayHandler)
    logger.info("event=gateway_start host=%s port=%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("gateway: shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
