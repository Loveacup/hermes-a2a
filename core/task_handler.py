"""A2A Task Handler — forward Tasks to Hermes agent loop.

Two execution modes (auto-selected):
1. API Server mode: POST /v1/runs on profile's API Server (for regent:8643, default:8642)
2. Subprocess mode: fallback for profiles without API Servers (hermes chat -q --profile <name>)

Result-classification keyword bank is externalised (P1-13):
    Priority: env A2A_CLASSIFY_KEYWORDS (path) > <hermes_home>/a2a-classify-keywords.json
              > built-in defaults below.
    JSON shape: {"tool_unavailable": [...], "task_achieved": [...]}
"""

import json, logging, os, shutil, subprocess, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

from identity import load_identity_prefix

logger = logging.getLogger("hermes-a2a.task_handler")

# Profile → API Server port (only profiles with running API Servers)
_API_SERVER_PORTS = {"regent": 8643, "default": 8642}
_API_TIMEOUT = 300

# ── Result classification ──────────────────────────────────────────────
# Heuristic signals in the agent's final response text.
# Scoped to send_message / delivery failures — NOT general "can't do X" analysis.
# Ordered: tool_unavailable checked FIRST (degraded > succeeded).
_DEFAULT_RESULT_SIGNALS: dict[str, list[str]] = {
    "tool_unavailable": [
        # English — send_message specific
        "unable to send", "cannot send", "can't send",
        "unable to deliver", "could not send", "failed to send",
        "conflict",  # Telegram polling conflict
        # Chinese — 发送特定
        "无法发送", "发送失败", "发送不了", "发不了",
        "发不出去", "无法投递",
    ],
    "task_achieved": [
        # English
        "sent", "delivered", "successful",
        "successfully",
        # Chinese
        "已发送", "已完成", "发送成功", "成功发送",
        "已送达", "已投递", "已发到", "已发出", "已发至",
    ],
}

_KEYWORDS_FILE_NAME = "a2a-classify-keywords.json"
_KEYWORDS_ENV_VAR = "A2A_CLASSIFY_KEYWORDS"
_RESULT_SIGNALS_CACHE: dict[str, list[str]] | None = None  # populated on first use


def _load_signals() -> dict[str, list[str]]:
    """Return the active keyword bank (cached after first call).

    Priority:
        1. env A2A_CLASSIFY_KEYWORDS → JSON file path
        2. <HERMES_HOME>/a2a-classify-keywords.json
        3. built-in _DEFAULT_RESULT_SIGNALS

    Malformed JSON falls back to defaults with a warning.  Unknown buckets
    are accepted (forward-compat for new categories); only ``tool_unavailable``
    and ``task_achieved`` are read by ``_classify``.
    """
    global _RESULT_SIGNALS_CACHE
    if _RESULT_SIGNALS_CACHE is not None:
        return _RESULT_SIGNALS_CACHE

    candidate: Path | None = None
    env_path = os.environ.get(_KEYWORDS_ENV_VAR, "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            candidate = p
    if candidate is None:
        home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
        fallback = Path(home) / _KEYWORDS_FILE_NAME
        if fallback.is_file():
            candidate = fallback

    if candidate is None:
        _RESULT_SIGNALS_CACHE = {k: list(v) for k, v in _DEFAULT_RESULT_SIGNALS.items()}
        return _RESULT_SIGNALS_CACHE

    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("classify keywords file must be a JSON object")
        loaded: dict[str, list[str]] = {}
        for bucket, items in data.items():
            if not isinstance(items, list):
                continue
            cleaned = [s for s in items if isinstance(s, str) and s.strip()]
            if cleaned:
                loaded[bucket] = cleaned
        # Ensure both default buckets present (per-bucket fallback).
        for k, default_vals in _DEFAULT_RESULT_SIGNALS.items():
            if k not in loaded:
                loaded[k] = list(default_vals)
        _RESULT_SIGNALS_CACHE = loaded
        logger.info(
            "[hermes-a2a] classify keywords loaded from %s (%d buckets)",
            candidate, len(loaded),
        )
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "[hermes-a2a] classify keywords file %s unreadable (%s); using defaults",
            candidate, e,
        )
        _RESULT_SIGNALS_CACHE = {k: list(v) for k, v in _DEFAULT_RESULT_SIGNALS.items()}

    return _RESULT_SIGNALS_CACHE


def _classify(status: str, response: str, error: str = "") -> dict:
    """Return semantic_status + completion_reason for the task result.

    semantic_status  ∈ {succeeded, degraded, failed}
    completion_reason ∈ {task_achieved, tool_unavailable, agent_error, timeout, unknown}
    """
    if status == "failed":
        if error and "timeout" in error.lower():
            return {"semantic_status": "failed", "completion_reason": "timeout"}
        return {"semantic_status": "failed", "completion_reason": "agent_error"}

    r = response.lower()
    signals = _load_signals()

    # degraded trumps succeeded — check first
    for sig in signals.get("tool_unavailable", []):
        if sig in r:
            return {"semantic_status": "degraded", "completion_reason": "tool_unavailable"}

    for sig in signals.get("task_achieved", []):
        if sig in r:
            return {"semantic_status": "succeeded", "completion_reason": "task_achieved"}

    return {"semantic_status": "succeeded", "completion_reason": "unknown"}


def handle_task(task: dict) -> dict:
    tid = task.get("id", "unknown")
    msg = task.get("message") or task.get("input") or task.get("action") or {}
    prompt = msg if isinstance(msg, str) else (msg.get("text") or msg.get("prompt") or _extract_from_parts(msg.get("parts", [])))
    if not prompt:
        task["status"] = "failed"
        task["error"] = "Empty message"
        return task
    
    profile = os.environ.get("HERMES_PROFILE", "")
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))

    # Identity prefix is owned by the deploying business — loaded from
    # env var / profile file / generic fallback.  See core/identity.py.
    if not prompt.startswith("【系统提示】"):
        identity_prefix = load_identity_prefix(hermes_home, profile)
        prompt = identity_prefix + prompt
    
    try:
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
                output_str = str(output)
                cls = _classify(task["status"], output_str, task.get("error", ""))
                task["semantic_status"] = cls["semantic_status"]
                task["completion_reason"] = cls["completion_reason"]
                task["artifact"] = {
                    "response": output_str,
                    "fallback_text": output_str,
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
    output = r.stdout.strip() or r.stderr.strip()
    task["status"] = "completed" if r.returncode == 0 else "failed"
    cls = _classify(task["status"], output, task.get("error", ""))
    task["semantic_status"] = cls["semantic_status"]
    task["completion_reason"] = cls["completion_reason"]
    task["artifact"] = {
        "response": output,
        "fallback_text": output,
        "duration_s": round(time.time() - start, 2),
        "mode": "subprocess",
    }
    return task

def _extract_from_parts(parts: list) -> str:
    for p in parts:
        if p.get("type") == "text":
            return p.get("text", "")
    return ""
