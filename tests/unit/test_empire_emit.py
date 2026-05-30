"""TDD RED: empire_emit.py — pre_tool_call hook 写入 empire-thread.jsonl.

验证点:
- register_emit_hook(ctx) 注册 pre_tool_call hook
- hook 将 tool call 事件写入 empire-thread.jsonl（append-only JSONL）
- 事件格式符合 empire_thread schema
- hook 非阻塞（单次 append < 5ms）
- hook 异常隔离（JSONL 不可写时不崩溃）
- 多 tool call 顺序写入、无丢失
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Mock PluginContext ──────────────────────────────────────────

class FakePluginContext:
    """模拟 Hermes PluginContext，提供 register_hook API。"""
    def __init__(self):
        self._hooks: dict[str, list] = {}

    def register_hook(self, hook_type: str, callback) -> None:
        self._hooks.setdefault(hook_type, []).append(callback)


# ── 工具函数 ────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(json.loads(line))
    return lines


# ══════════════════════════════════════════════════════════════════
# RED 测试（先写测试，后写实现）
# ══════════════════════════════════════════════════════════════════

class TestEmpireEmitHook:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """每个测试独立的 HERMES_HOME 和 JSONL 路径。"""
        self.home = tmp_path / "hermes"
        self.profile = "regent"
        self.jsonl_path = self.home / "empire-thread.jsonl"
        self.home.mkdir(parents=True, exist_ok=True)

        # 注入环境变量（HERMES_HOME 已 profile-specific；HERMES_PROFILE 可选）
        os.environ["HERMES_HOME"] = str(self.home)
        os.environ["HERMES_PROFILE"] = self.profile

        self.ctx = FakePluginContext()
        yield

        # 清理
        os.environ.pop("HERMES_HOME", None)
        os.environ.pop("HERMES_PROFILE", None)

    # ── 测试 1: hook 注册 ────────────────────────────────────

    def test_register_adds_pre_tool_call_hook(self):
        """register_emit_hook(ctx) 应向 ctx 注册 pre_tool_call hook。"""
        from empire_emit import register_emit_hook  # type: ignore

        register_emit_hook(self.ctx)

        assert "pre_tool_call" in self.ctx._hooks
        assert len(self.ctx._hooks["pre_tool_call"]) >= 1

    # ── 测试 2: 事件写入 JSONL ───────────────────────────────

    def test_hook_writes_event_to_jsonl(self):
        """pre_tool_call hook 调用后应写入 empire-thread.jsonl。"""
        from empire_emit import register_emit_hook, _emit_handler  # type: ignore

        register_emit_hook(self.ctx)
        hook = self.ctx._hooks["pre_tool_call"][0]

        # 触发 hook
        result = hook(
            tool_name="terminal",
            args={"command": "echo hello"},
            task_id="t_test_001",
            run_id="r_test_001",
        )

        # hook 不应拦截（返回 None 放行）
        assert result is None

        # 验证 JSONL 写入
        events = _read_jsonl(self.jsonl_path)
        assert len(events) >= 1, f"Expected at least 1 event, got 0. JSONL path: {self.jsonl_path}"

        evt = events[0]
        assert evt["event_type"] == "execute"
        assert evt["profile"] == "regent"
        assert "event_id" in evt
        assert "timestamp" in evt
        assert evt["content"]["tool_name"] == "terminal"
        assert evt["task_id"] == "t_test_001"
        assert evt["run_id"] == "r_test_001"

    # ── 测试 3: 事件 ID 唯一 ─────────────────────────────────

    def test_each_event_has_unique_id(self):
        """每个事件应生成唯一 event_id。"""
        from empire_emit import register_emit_hook  # type: ignore

        register_emit_hook(self.ctx)
        hook = self.ctx._hooks["pre_tool_call"][0]

        # 连续触发 5 次
        for i in range(5):
            hook(
                tool_name=f"tool_{i}",
                args={},
                task_id=f"t_{i}",
                run_id="r_test",
            )

        events = _read_jsonl(self.jsonl_path)
        assert len(events) == 5

        ids = [e["event_id"] for e in events]
        assert len(set(ids)) == 5, f"Duplicate event_ids: {ids}"

    # ── 测试 4: 非阻塞性能 ───────────────────────────────────

    def test_hook_is_fast_non_blocking(self):
        """单次 hook 调用应在 5ms 内完成（append-only JSONL）。"""
        from empire_emit import register_emit_hook  # type: ignore

        register_emit_hook(self.ctx)
        hook = self.ctx._hooks["pre_tool_call"][0]

        # 预热一次（文件创建 + 目录缓存）
        hook(tool_name="warmup", args={}, task_id="t_warm", run_id="r_warm")

        # 测量 10 次
        times = []
        for i in range(10):
            start = time.perf_counter()
            hook(tool_name=f"bench_{i}", args={}, task_id=f"t_bench_{i}", run_id="r_bench")
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

        # 统计
        avg_ms = sum(times) / len(times)
        max_ms = max(times)
        assert avg_ms < 5.0, f"Average too slow: {avg_ms:.2f}ms (max {max_ms:.2f}ms)"
        # 单次最大不超过 20ms（允许首次 flush 开销）
        assert max_ms < 20.0, f"Single call too slow: {max_ms:.2f}ms"

    # ── 测试 5: 异常隔离 ─────────────────────────────────────

    def test_hook_survives_unwritable_directory(self, monkeypatch):
        """JSONL 路径不可写时 hook 不应崩溃，应静默返回 None。"""
        from empire_emit import register_emit_hook  # type: ignore

        register_emit_hook(self.ctx)
        hook = self.ctx._hooks["pre_tool_call"][0]

        # 用 monkeypatch 强制 path.open 抛异常
        original_open = Path.open

        def _failing_open(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "open", _failing_open)

        try:
            result = hook(
                tool_name="terminal",
                args={},
                task_id="t_test",
                run_id="r_test",
            )
            # 不应抛异常，应返回 None 放行
            assert result is None
        except Exception as e:
            pytest.fail(f"Hook crashed on unwritable JSONL: {e}")
        finally:
            monkeypatch.setattr(Path, "open", original_open)

    # ── 测试 6: 事件格式完整性 ──────────────────────────────

    def test_event_schema_completeness(self):
        """验证事件包含 empire_thread 必需字段。"""
        from empire_emit import register_emit_hook  # type: ignore

        register_emit_hook(self.ctx)
        hook = self.ctx._hooks["pre_tool_call"][0]

        hook(
            tool_name="web_search",
            args={"query": "test"},
            task_id="t_schema",
            run_id="r_schema",
        )

        events = _read_jsonl(self.jsonl_path)
        evt = events[0]

        # empire_thread 必需字段
        required = ["event_id", "timestamp", "profile", "event_type", "content", "run_id"]
        for field in required:
            assert field in evt, f"Missing required field: {field}"

        # timestamp 应是 ISO 8601 格式
        assert "T" in evt["timestamp"]
        assert "+" in evt["timestamp"] or "Z" in evt["timestamp"] or evt["timestamp"].endswith("00:00")

        # content 应包含工具调用信息
        assert "tool_name" in evt["content"]
        assert "args" in evt["content"]

    # ── 测试 7: 并发安全（多 handler 不互相干扰）───────────────

    def test_multiple_handlers_dont_interfere(self):
        """多个 pre_tool_call handler 共存时不应互相干扰。"""
        from empire_emit import register_emit_hook  # type: ignore

        # 注册两个 emit handler（模拟两个 profile 场景）
        register_emit_hook(self.ctx)
        # 再注册一次（幂等？或第二个实例）
        register_emit_hook(self.ctx)  # 应安全处理

        hooks = self.ctx._hooks["pre_tool_call"]
        assert len(hooks) >= 1  # 至少有一个

        # 两个 handler 各触发一次
        hooks[0](tool_name="tool_a", args={}, task_id="t_a", run_id="r_a")
        if len(hooks) > 1:
            hooks[1](tool_name="tool_b", args={}, task_id="t_b", run_id="r_b")

        events = _read_jsonl(self.jsonl_path)
        assert len(events) >= 2
        tool_names = {e["content"]["tool_name"] for e in events}
        assert "tool_a" in tool_names
        if len(hooks) > 1:
            assert "tool_b" in tool_names
