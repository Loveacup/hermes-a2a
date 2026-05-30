#!/usr/bin/env python3
"""1-step A2A smoke test — verify auth fix"""
import urllib.request
import json
import subprocess
import time
from pathlib import Path

TOKEN = Path("/Users/alexcai/.hermes/.a2a-token").read_text().strip()
PORT = int(subprocess.run(
    ["python3", "/Users/alexcai/.hermes/plugins/hermes-a2a/registry.py", "port", "planner"],
    capture_output=True, text=True
).stdout.strip())

tid = "smoke-{}".format(int(time.time()))
payload = json.dumps({"id": tid, "task": "Reply with exactly: OK"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:{}/a2a/tasks".format(PORT),
    data=payload,
    headers={"Authorization": "Bearer {}".format(TOKEN), "Content-Type": "application/json"},
    method="POST"
)
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())
print("POST -> id={} status={}".format(data.get("id"), data.get("status")))

deadline = time.time() + 120
while time.time() < deadline:
    req2 = urllib.request.Request(
        "http://127.0.0.1:{}/a2a/tasks/{}".format(PORT, tid),
        headers={"Authorization": "Bearer {}".format(TOKEN)}
    )
    with urllib.request.urlopen(req2, timeout=10) as r:
        data = json.loads(r.read())
    status = data.get("status", "")
    if status in ("completed", "failed", "cancelled"):
        resp = data.get("artifact", {}).get("response", "")[:200]
        print("POLL -> status={} response={}".format(status, resp))
        break
    print("  waiting... ({})".format(status))
    time.sleep(10)
else:
    print("TIMEOUT after 120s")
