#!/usr/bin/env python3
"""Auto-generate A2A Agent Card from Hermes profile config."""

import json, os
from pathlib import Path

SKILL_MAP = {"terminal": "shell-execution", "file": "file-operations", "web": "web-research",
             "browser": "browser-automation", "delegation": "task-delegation",
             "kanban": "kanban-workflow", "memory": "persistent-memory",
             "vision": "image-analysis", "image_gen": "image-generation",
             "code_execution": "code-execution", "session_search": "session-search",
             "cronjob": "scheduled-tasks"}

def generate_agent_card(hermes_home: str) -> dict:
    profile = os.environ.get("HERMES_PROFILE", "default")
    host = os.environ.get("A2A_HOST", "127.0.0.1")
    port = int(os.environ.get("A2A_PORT", "8650"))
    config = _load_config(hermes_home)
    toolsets = config.get("toolsets", [])
    model = config.get("model", {})
    return {
        "name": f"Hermes Agent — {profile}",
        "description": _load_description(hermes_home),
        "url": f"http://{host}:{port}/a2a",
        "provider": {"organization": "三省六部 (Three Provinces Six Ministries)", "url": "https://github.com/NousResearch/hermes-agent"},
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text", "file"],
        "defaultOutputModes": ["text", "file"],
        "skills": [{"id": v, "description": k} for k, v in SKILL_MAP.items() if k in toolsets] + [{"id": "health-check", "description": "Service health and status reporting"}],
        "currentModel": {"default": model.get("default", "unknown"), "provider": model.get("provider", "unknown")},
        "version": "0.1.0",
        "protocolVersion": "1.0"
    }

def _load_config(hermes_home: str) -> dict:
    path = Path(hermes_home) / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        toolsets, model = [], {}
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s.startswith("- ") and s[2:] not in ("hermes-cli", "hermes-telegram"):
                    toolsets.append(s[2:])
                if "default:" in s and "model:" not in line and not s.startswith(" "*4):
                    model["default"] = s.split(":", 1)[1].strip().strip("'\"")
                if "provider:" in s and "default:" not in s and "fallback" not in s:
                    model["provider"] = s.split(":", 1)[1].strip().strip("'\"")
        return {"toolsets": toolsets, "model": model}

def _load_description(hermes_home: str) -> str:
    soul = Path(hermes_home) / "SOUL.md"
    if soul.exists():
        for line in soul.read_text().split("\n"):
            line = line.strip()
            if line and not line.startswith("---") and not line.startswith("#"):
                return line[:200]
    return f"Hermes Agent profile: {os.environ.get('HERMES_PROFILE', 'default')}"

if __name__ == "__main__":
    hh = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    print(json.dumps(generate_agent_card(hh), indent=2, ensure_ascii=False))
