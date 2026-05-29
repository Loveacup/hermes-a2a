"""Supermemory Sink: 长期记忆后端（替代 Hindsight 双层架构）.

ADR-005: 三省六部决定 Supermemory 为唯一长期记忆后端.

行为:
- write(event) → 渲染 markdown → POST /v1/documents
- container_tag 从 ~/.hermes/supermemory.json 解析:
    regent → hermes-cabinet
    default → hermes
    其他 profile → fallback "hermes"
- accept(event) → 拒绝 _source=sink_writeback（同 Obsidian，防回路）
- 失败容错: 单事件投递失败不影响 cursor 推进; 异常被吞掉并 log，
  保留与 Obsidian sink 相同的 "best effort" 语义.

API key: SUPERMEMORY_API_KEY env.
传输: urllib.request 直发，无 SDK 依赖（同 hindsight.py 风格）.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from ..core import Event, Sink
from ..paths import hermes_home

log = logging.getLogger("event_bridge.sinks.supermemory")

_DEFAULT_CONTAINER_TAG = "hermes"
_DEFAULT_API_URL = "https://api.supermemory.ai"


def _load_profile_map() -> dict[str, str]:
    """读取 ~/.hermes/supermemory.json，返回 profile → container_tag 映射."""
    cfg = hermes_home() / "supermemory.json"
    if not cfg.exists():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        return {}
    out: dict[str, str] = {}
    for name, entry in profiles.items():
        if isinstance(entry, dict) and entry.get("container_tag"):
            out[name] = str(entry["container_tag"])
    return out


class TransportError(Exception):
    """Supermemory 投递失败统一抛此异常."""


class HttpTransport:
    """直发 POST /v3/documents，无 SDK 依赖."""

    def __init__(self, url: str, api_key: str, timeout: float = 5.0):
        self.url = url.rstrip("/") + "/v3/documents"
        self.api_key = api_key
        self.timeout = timeout

    def add_document(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {"ok": True}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TransportError(str(e)) from e


class SupermemorySink(Sink):
    name = "supermemory"

    def __init__(self, *, transport: Any = None,
                 profile_map: dict[str, str] | None = None):
        self._profile_map = (profile_map
                             if profile_map is not None
                             else _load_profile_map())
        self._transport = transport or self._build_default_transport()

    @staticmethod
    def _build_default_transport() -> HttpTransport:
        url = os.environ.get("SUPERMEMORY_API_URL", _DEFAULT_API_URL)
        key = os.environ.get("SUPERMEMORY_API_KEY", "")
        return HttpTransport(url=url, api_key=key)

    def _container_tag(self, profile: str) -> str:
        return self._profile_map.get(profile, _DEFAULT_CONTAINER_TAG)

    def write(self, evt: Event) -> None:
        tag = self._container_tag(evt.profile)
        payload: dict[str, Any] = {
            "content": _render(evt),
            "containerTags": [tag],
            "metadata": {
                "event_type": evt.event_type or "unknown",
                "profile": evt.profile,
                "timestamp": evt.timestamp,
            },
        }
        if evt.event_id:
            payload["customId"] = evt.event_id
        if evt.task_id:
            payload["metadata"]["task_id"] = evt.task_id
        try:
            self._transport.add_document(payload)
        except TransportError as e:
            log.warning("SupermemorySink add failed (profile=%s eid=%s): %s",
                        evt.profile, evt.event_id, e)


def _render(evt: Event) -> str:
    et = evt.event_type or "unknown"
    head = [
        f"# {et.upper()} — {evt.timestamp}",
        "",
        f"- event_id: {evt.event_id}",
        f"- profile: {evt.profile}",
        f"- timestamp: {evt.timestamp}",
    ]
    if evt.task_id:
        head.append(f"- task_id: {evt.task_id}")
    body = [
        "",
        "## Content",
        "",
        "```json",
        json.dumps(evt.content, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(head + body) + "\n"
