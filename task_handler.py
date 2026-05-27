"""A2A Task Handler — forward Tasks to Hermes agent loop.

Two execution modes (auto-selected):
1. API Server mode: POST /v1/runs on profile's API Server (for regent:8643, default:8642)
2. Subprocess mode: fallback for profiles without API Servers (hermes chat -q --profile <name>)
"""

import json, logging, os, shutil, subprocess, time, urllib.request, urllib.error
from datetime import datetime, timezone

logger = logging.getLogger("hermes-a2a.task_handler")

# Profile → API Server port (only profiles with running API Servers)
_API_SERVER_PORTS = {"regent": 8643, "default": 8642}
_API_TIMEOUT = 300


def handle_task(task: dict) -> dict:
    tid = task.get("id", "unknown")
    msg = task.get("message", {})
    prompt = msg if isinstance(msg, str) else (msg.get("text") or _extract_from_parts(msg.get("parts", [])))
    if not prompt:
        task["status"] = "failed"
        task["error"] = "Empty message"
        return task
    try:
        profile = os.environ.get("HERMES_PROFILE", "")
        if profile in _API_SERVER_PORTS:
            return _via_api_server(task, tid, prompt, profile)
        return _via_subprocess(task, tid, prompt, profile)
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        return task


def _via_api_server(task: dict, tid: str, prompt: str, profile: str) -> dict:
    """Execute task via Hermes /v1/runs API (thin adapter mode)."""
    port = _API_SERVER_PORTS[profile]
    start = time.time()

    # Create run
    body = json.dumps({"input": prompt, "model": "hermes-agent"}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/runs",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        run = json.loads(resp.read())
        run_id = run.get("run_id") or run.get("id")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning(f"[hermes-a2a] API Server {profile}:{port} unreachable, falling back to subprocess: {e}")
        return _via_subprocess(task, tid, prompt, profile)

    # Poll for completion
    deadline = start + _API_TIMEOUT
    while time.time() < deadline:
        time.sleep(1)
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/runs/{run_id}", timeout=5
            )
            run = json.loads(resp.read())
            status = run.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                task["status"] = "completed" if status == "completed" else "failed"
                output = run.get("output") or run.get("response", "")
                if isinstance(output, list):
                    output = "\n".join(
                        m.get("content", "") for m in output
                        if isinstance(m, dict) and m.get("type") == "message"
                    )
                task["artifact"] = {
                    "response": str(output),
                    "duration_s": round(time.time() - start, 2),
                    "run_id": run_id,
                    "mode": "api_server",
                }
                return task
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            continue

    task["status"] = "failed"
    task["error"] = f"Timeout after {_API_TIMEOUT}s"
    return task


def _hermes_bin() -> str:
    """Resolve hermes CLI path. Check PATH first, then common install locations."""
    hermes = shutil.which("hermes")
    if hermes:
        return hermes
    candidates = [
        os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes"),
        os.path.expanduser("~/.hermes/venv/bin/hermes"),
        "/opt/homebrew/bin/hermes",
        "/usr/local/bin/hermes",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "hermes"  # fallback — will fail with clear error


def _via_subprocess(task: dict, tid: str, prompt: str, profile: str) -> dict:
    """Execute task via hermes chat subprocess (fallback mode)."""
    start = time.time()
    cmd = [_hermes_bin(), "chat", "-q", prompt, "--quiet"]
    if profile:
        cmd += ["--profile", profile]
    # Pass parent env + ensure API keys are available from the main .env
    env = os.environ.copy()
    main_env = os.path.expanduser("~/.hermes/.env")
    if os.path.isfile(main_env):
        for line in open(main_env):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k not in env:
                    env[k] = v.strip().strip('"').strip("'")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    task["status"] = "completed" if r.returncode == 0 else "failed"
    task["artifact"] = {
        "response": r.stdout.strip() or r.stderr.strip(),
        "duration_s": round(time.time() - start, 2),
        "mode": "subprocess",
    }
    return task

def _extract_from_parts(parts: list) -> str:
    for p in parts:
        if p.get("type") == "text":
            return p.get("text", "")
    return ""
