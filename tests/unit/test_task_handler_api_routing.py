"""T2 RED: task_handler.py — API Server 路由全面铺开 + fallback.

新规约:
- 所有 profile（不再有"白名单"）尝试 API Server
- API Server 端口来自 port_resolver.api_server_port(profile)
- 任何 profile 的 API Server 不可达 → 透明 fallback 到 subprocess
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch, MagicMock

import pytest


# ── R8: 所有 profile 走 API Server 路径（无白名单） ───────────

def test_th_t1_no_hardcoded_whitelist():
    """task_handler 不应再持有 _API_SERVER_PORTS 字典."""
    import task_handler
    assert not hasattr(task_handler, "_API_SERVER_PORTS"), \
        "硬编码白名单 _API_SERVER_PORTS 应已移除"


def test_th_t2_port_resolved_dynamically():
    """task_handler 应通过 port_resolver 模块取端口."""
    import task_handler
    from port_resolver import api_server_port
    # 暴露一个统一的查询函数（便于其他模块共用）
    assert hasattr(task_handler, "_api_server_port"), \
        "task_handler 应暴露 _api_server_port(profile) helper"
    assert task_handler._api_server_port("engineer") == api_server_port("engineer")


# ── R9: 连接错误 → subprocess fallback（任意 profile） ────────

def test_th_t3_connect_error_falls_back_to_subprocess(monkeypatch):
    import task_handler

    # 阻断真实 hermes CLI 启动
    fake_completed = MagicMock(returncode=0,
                               stdout="ok",
                               stderr="")

    def fake_run(*args, **kwargs):
        return fake_completed

    monkeypatch.setattr(task_handler.subprocess, "run", fake_run)

    # 强制 urlopen 抛 URLError
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(task_handler.urllib.request, "urlopen", fake_urlopen)

    task = {"id": "t1", "summary": "test", "description": "x"}
    result = task_handler._via_api_server(task, "t1", "do thing", "engineer")
    # subprocess fallback 路径会把 stdout 放进 artifact
    assert result.get("artifact", {}).get("mode") == "subprocess"


# ── R10: API Server 成功路径不走 subprocess ──────────────────

def test_th_t4_api_server_success_path(monkeypatch):
    import task_handler

    sequence = iter([
        # 1) POST /v1/runs → run_id
        json.dumps({"run_id": "r1"}).encode(),
        # 2) GET /v1/runs/r1 → completed
        json.dumps({"status": "completed",
                    "output": "done"}).encode(),
    ])

    class FakeResp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, *args, **kwargs):
        return FakeResp(next(sequence))

    monkeypatch.setattr(task_handler.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(task_handler.time, "sleep", lambda s: None)

    task = {"id": "t1"}
    result = task_handler._via_api_server(task, "t1", "say hi", "engineer")
    assert result["status"] == "completed"
    assert result["artifact"]["mode"] == "api_server"
