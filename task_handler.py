"""A2A Task Handler — forward Tasks to Hermes agent loop."""

import json, logging, os, subprocess, time
from datetime import datetime, timezone

logger = logging.getLogger("hermes-a2a.task_handler")

def handle_task(task: dict) -> dict:
    tid = task.get("id", "unknown")
    msg = task.get("message", {})
    prompt = msg if isinstance(msg, str) else (msg.get("text") or _extract_from_parts(msg.get("parts", [])))
    if not prompt:
        task["status"] = "failed"
        task["error"] = "Empty message"
        return task
    try:
        start = time.time()
        profile = os.environ.get("HERMES_PROFILE")
        cmd = ["hermes", "chat", "-q", prompt, "--quiet"]
        if profile:
            cmd += ["--profile", profile]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        task["status"] = "completed"
        task["artifact"] = {"response": r.stdout.strip(), "duration_s": round(time.time()-start, 2)}
    except subprocess.TimeoutExpired:
        task["status"] = "failed"
        task["error"] = "Timeout after 300s"
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
    return task

def _extract_from_parts(parts: list) -> str:
    for p in parts:
        if p.get("type") == "text":
            return p.get("text", "")
    return ""
