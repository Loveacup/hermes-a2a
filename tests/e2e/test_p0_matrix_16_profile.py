#!/usr/bin/env python3
"""E2E P0: 16-profile API Server matrix.

For every profile in port-map.md, POST a real LLM task to its A2A endpoint,
poll until completion, and assert:
  1. status == "completed"
  2. artifact.mode == "api_server"  (NOT subprocess fallback)
  3. response mentions the profile name (worker knows its identity)

Runs profiles serially to avoid process-storm — each task is full LLM call,
so total runtime ~16 × (3-15s) ≈ a few minutes.

Output:
  - stdout: per-profile result line
  - /tmp/e2e-matrix-results.log: machine-readable JSONL summary

Usage:
  HOME=/Users/alexcai HERMES_HOME=/Users/alexcai/.hermes \
    /Users/alexcai/.hermes/hermes-agent/venv/bin/python3 \
    tests/e2e/test_p0_matrix_16_profile.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PORT_MAP = ROOT / "s6m-config" / "port-map.md"
TOKEN_PATH = Path("/Users/alexcai/.hermes/.a2a-token")
RESULTS_LOG = Path("/tmp/e2e-matrix-results.log")

POLL_INTERVAL_S = 3
PER_TASK_TIMEOUT_S = 120
PROMPT = (
    "请用一句话回答：你的 profile 名是什么？"
    "只回答 profile 名（小写英文单词），不要其他内容。"
)


# ─── port-map parsing ───────────────────────────────────────────

A2A_LINE = re.compile(r"^- \*\*([a-z_]+)\*\*.*?端口 `(\d+)`")


def load_a2a_ports() -> dict[str, int]:
    """Parse the 16 A2A entries (A2A range 8650-8949) from port-map.md.

    The same regex matches the API Server block too; filter by port range.
    """
    ports: dict[str, int] = {}
    for line in PORT_MAP.read_text(encoding="utf-8").splitlines():
        m = A2A_LINE.match(line)
        if not m:
            continue
        profile, port_s = m.group(1), int(m.group(2))
        if 8650 <= port_s <= 8949 and profile not in ports:
            ports[profile] = port_s
    return ports


# ─── A2A client helpers ─────────────────────────────────────────

def _read_token() -> str:
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def post_task(port: int, token: str, text: str) -> str | None:
    body = json.dumps({"message": {"text": text}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/a2a/tasks",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError) as e:
        print(f"      ❌ POST failed: {e}")
        return None
    try:
        data = json.loads(resp.read())
    except json.JSONDecodeError as e:
        print(f"      ❌ POST returned non-JSON: {e}")
        return None
    return data.get("id")


def poll_task(port: int, token: str, tid: str, deadline: float) -> dict | None:
    req_url = f"http://127.0.0.1:{port}/a2a/tasks/{tid}"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                req_url, headers={"Authorization": f"Bearer {token}"}
            )
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            status = data.get("status")
            if status in ("completed", "failed", "cancelled"):
                return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(POLL_INTERVAL_S)
    return None  # timeout


# ─── per-profile check ──────────────────────────────────────────

def check_profile(profile: str, port: int, token: str) -> dict:
    result: dict = {
        "profile": profile,
        "a2a_port": port,
        "status": None,
        "mode": None,
        "duration_s": None,
        "mentions_profile": None,
        "response_preview": "",
        "error": None,
        "pass": False,
    }
    tid = post_task(port, token, PROMPT)
    if not tid:
        result["error"] = "post_failed"
        return result
    result["task_id"] = tid

    data = poll_task(port, token, tid, deadline=time.time() + PER_TASK_TIMEOUT_S)
    if data is None:
        result["error"] = "poll_timeout"
        return result

    result["status"] = data.get("status")
    artifact = data.get("artifact") or {}
    result["mode"] = artifact.get("mode")
    result["duration_s"] = artifact.get("duration_s")
    response = str(artifact.get("response") or "")
    result["response_preview"] = response[:160]
    result["mentions_profile"] = profile.lower() in response.lower()

    result["pass"] = (
        result["status"] == "completed"
        and result["mode"] == "api_server"
        and result["mentions_profile"]
    )
    return result


# ─── main ────────────────────────────────────────────────────────

def main() -> int:
    if not TOKEN_PATH.is_file():
        print(f"❌ A2A token missing: {TOKEN_PATH}")
        return 2
    if not PORT_MAP.is_file():
        print(f"❌ port-map missing: {PORT_MAP}")
        return 2

    token = _read_token()
    ports = load_a2a_ports()
    profiles = sorted(ports.keys())
    if len(profiles) != 16:
        print(f"⚠️  Expected 16 profiles, got {len(profiles)}: {profiles}")

    print("=" * 72)
    print(f"P0 E2E — 16-profile API Server matrix ({len(profiles)} profiles)")
    print("=" * 72)

    overall: list[dict] = []
    pass_count = 0
    for idx, profile in enumerate(profiles, 1):
        port = ports[profile]
        print(f"\n[{idx:2d}/{len(profiles)}] {profile} (A2A :{port})")
        t0 = time.time()
        r = check_profile(profile, port, token)
        dt = time.time() - t0
        overall.append(r)
        tag = "✅" if r["pass"] else "❌"
        print(
            f"   {tag} status={r['status']} mode={r['mode']} "
            f"mentions={r['mentions_profile']} dur={r['duration_s']}s wall={dt:.1f}s"
        )
        if r["response_preview"]:
            preview = r["response_preview"].replace("\n", " ")
            print(f"      resp: {preview}")
        if r["error"]:
            print(f"      err:  {r['error']}")
        if r["pass"]:
            pass_count += 1

    # Write machine-readable summary
    with RESULTS_LOG.open("w", encoding="utf-8") as f:
        for r in overall:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("\n" + "=" * 72)
    print(
        f"MATRIX RESULT: {pass_count}/{len(profiles)} passed "
        f"(api_server mode + identity confirmed)"
    )
    print(f"Results log:   {RESULTS_LOG}")
    print("=" * 72)
    if pass_count < len(profiles):
        print("\nFailures:")
        for r in overall:
            if not r["pass"]:
                print(
                    f"  {r['profile']}: status={r['status']} mode={r['mode']} "
                    f"mentions={r['mentions_profile']} err={r['error']}"
                )
    return 0 if pass_count == len(profiles) else 1


if __name__ == "__main__":
    sys.exit(main())
