#!/usr/bin/env python3
"""Auto-generate A2A Agent Card from Hermes profile config."""

import json, os
from pathlib import Path

SKILL_MAP = {
    "terminal":       {"id": "shell-execution",    "name": "Shell Execution",       "description": "Execute shell commands and scripts", "examples": ["run tests", "deploy app", "manage processes"], "tags": ["cli", "automation"]},
    "file":           {"id": "file-operations",    "name": "File Operations",       "description": "Read, write, and manage files", "examples": ["edit config", "create document", "search codebase"], "tags": ["filesystem", "editing"]},
    "web":            {"id": "web-research",       "name": "Web Research",          "description": "Search and extract web content", "examples": ["find documentation", "check news", "research topic"], "tags": ["internet", "search"]},
    "browser":        {"id": "browser-automation", "name": "Browser Automation",    "description": "Control web browsers programmatically", "examples": ["fill forms", "scrape data", "screenshot page"], "tags": ["browser", "automation"]},
    "delegation":     {"id": "task-delegation",    "name": "Task Delegation",       "description": "Delegate work to sub-agents", "examples": ["parallel code review", "multi-agent research"], "tags": ["orchestration", "multi-agent"]},
    "kanban":         {"id": "kanban-workflow",    "name": "Kanban Workflow",       "description": "Manage multi-step workflows via Kanban board", "examples": ["track project phases", "dispatch parallel tasks"], "tags": ["workflow", "project-management"]},
    "memory":         {"id": "persistent-memory",  "name": "Persistent Memory",     "description": "Store and recall long-term information", "examples": ["remember user preferences", "track project context"], "tags": ["storage", "context"]},
    "vision":         {"id": "image-analysis",     "name": "Image Analysis",        "description": "Analyze and describe images", "examples": ["OCR document", "identify objects", "read charts"], "tags": ["vision", "multimodal"]},
    "image_gen":      {"id": "image-generation",   "name": "Image Generation",      "description": "Generate images from text descriptions", "examples": ["create diagram", "design mockup", "generate art"], "tags": ["generation", "creative"]},
    "code_execution": {"id": "code-execution",     "name": "Code Execution",        "description": "Execute Python code with tool access", "examples": ["data analysis", "batch processing", "automation scripts"], "tags": ["code", "scripting"]},
    "session_search": {"id": "session-search",     "name": "Session Search",        "description": "Search past conversation history", "examples": ["recall past decisions", "find previous context"], "tags": ["history", "context"]},
    "cronjob":        {"id": "scheduled-tasks",    "name": "Scheduled Tasks",       "description": "Schedule recurring background jobs", "examples": ["daily report", "periodic health check", "scheduled sync"], "tags": ["scheduling", "automation"]},
}

# Base toolsets always available in any Hermes profile (built-in, not in config toolsets list)
_BASE_TOOLSETS = {"terminal", "file", "web", "browser", "delegation", "kanban",
                  "memory", "vision", "image_gen", "code_execution", "session_search", "cronjob"}

def generate_agent_card(hermes_home: str) -> dict:
    profile = os.environ.get("HERMES_PROFILE", "default")
    host = os.environ.get("A2A_HOST", "127.0.0.1")
    port = int(os.environ.get("A2A_PORT", "8650"))
    config = _load_config(hermes_home)
    config_toolsets = set(config.get("toolsets", [])) - {"hermes-cli", "hermes-telegram"}
    # If config explicitly declares toolsets (beyond CLI wrappers), use those.
    # Otherwise, merge base toolsets (main profiles have all built-ins).
    toolsets = config_toolsets if config_toolsets else _BASE_TOOLSETS
    model = config.get("model", {})
    return {
        "name": f"Hermes Agent — {profile}",
        "description": _load_description(hermes_home),
        "url": f"http://{host}:{port}/a2a",
        "provider": {"organization": "三省六部 (Three Provinces Six Ministries)", "url": "https://github.com/NousResearch/hermes-agent"},
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text", "file"],
        "defaultOutputModes": ["text", "file"],
        "skills": [SKILL_MAP[k] for k in SKILL_MAP if k in toolsets] + [
            {"id": "health-check", "name": "Health Check", "description": "Service health and status reporting",
             "examples": ["check endpoint status", "verify service health"], "tags": ["monitoring", "infrastructure"]}
        ],
        "currentModel": {"default": model.get("default", "unknown"), "provider": model.get("provider", "unknown")},
        "version": "0.1.0",
        "protocolVersion": "1.0"
    }

def _load_config(hermes_home: str) -> dict:
    import yaml
    path = Path(hermes_home) / "config.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}

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
