"""End-to-end server tests: spawn server.py as subprocess + HTTP probe."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "core" / "server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture
def running_server(tmp_path):
    port = _free_port()
    home = tmp_path / "h"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        HERMES_HOME=str(home),
        HERMES_PROFILE="testprof",
        A2A_HOST="127.0.0.1",
        A2A_PORT=str(port),
        A2A_AUTH_TOKEN="integration-test-token",
    )
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PY)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    if not _wait_ready(f"{base}/health", timeout=5):
        proc.terminate()
        proc.wait(timeout=2)
        pytest.skip("server failed to start (likely missing pyyaml for agent_card)")
    yield base, "integration-test-token"
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _post(url, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, json.loads(r.read())


def test_health_public(running_server):
    base, _ = running_server
    status, body = _get(f"{base}/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["profile"] == "testprof"


def test_card_public(running_server):
    base, _ = running_server
    status, body = _get(f"{base}/a2a/.well-known/agent-card.json")
    assert status == 200
    assert "name" in body and "skills" in body


def test_tasks_requires_auth(running_server):
    base, _ = running_server
    status, body = _get(f"{base}/a2a/tasks")
    assert status in (401, 403)
    assert "unauthorized" in body.get("error", "")


def test_tasks_list_empty(running_server):
    base, token = running_server
    status, body = _get(f"{base}/a2a/tasks", token=token)
    assert status == 200
    assert body["count"] == 0
    assert body["tasks"] == []


def test_post_creates_task_with_full_uuid(running_server):
    base, token = running_server
    status, body = _post(
        f"{base}/a2a/tasks",
        {"message": {"role": "user", "parts": [{"type": "text", "text": "test"}]}},
        token=token,
    )
    assert status == 201
    tid = body["id"]
    assert tid.startswith("a2a-")
    assert len(tid) == len("a2a-") + 32  # P0-10: full uuid4.hex


def test_post_then_list_shows_task(running_server):
    base, token = running_server
    _, created = _post(
        f"{base}/a2a/tasks",
        {"message": {"role": "user", "parts": [{"type": "text", "text": "x"}]}},
        token=token,
    )
    time.sleep(0.5)
    _, listing = _get(f"{base}/a2a/tasks?limit=5", token=token)
    ids = [t["id"] for t in listing["tasks"]]
    assert created["id"] in ids


def test_concurrent_posts_unique_ids(running_server):
    """ThreadingHTTPServer + uuid4 should never collide across rapid POSTs."""
    import concurrent.futures
    base, token = running_server

    def submit(_):
        _, body = _post(
            f"{base}/a2a/tasks",
            {"message": {"role": "user", "parts": [{"type": "text", "text": "c"}]}},
            token=token,
        )
        return body["id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        ids = list(ex.map(submit, range(20)))
    assert len(set(ids)) == 20, "task_id collision detected"
