"""W2 RED: sinks/hindsight.py — Hindsight Sink + 4 层降级.

L0 实时(2s 超时)
L1 重试 1/4/16s
L2 DLQ
L3 熔断 60s

测试用注入式 Transport + Clock：完全无网络/无 sleep.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from event_bridge.core import Event  # type: ignore
from event_bridge.sinks.hindsight import (  # type: ignore
    HindsightSink,
    TransportError,
)


# ── 注入式 Transport ──────────────────────────────────────────

class StubTransport:
    """模拟 Hindsight REST 客户端."""
    def __init__(self, *, fail_times: int = 0, always_fail: bool = False):
        self.fail_times = fail_times
        self.always_fail = always_fail
        self.calls: list[dict] = []
        self.fail_count = 0

    def put_memory(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.always_fail or self.fail_count < self.fail_times:
            self.fail_count += 1
            raise TransportError("simulated")
        return {"ok": True, "memory_id": f"m{len(self.calls)}"}


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ── 工具 ──────────────────────────────────────────────────────

def _evt(eid="ev1", ts="2026-05-30T00:00:00Z",
         et="edict", profile="regent", source=""):
    raw = {
        "event_id": eid,
        "event_type": et,
        "timestamp": ts,
        "content": {"content": "hi", "issuer": profile},
        "run_id": "r1",
    }
    if source:
        raw["_source"] = source
    return Event(raw=raw, profile=profile)


@pytest.fixture
def eb_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_BRIDGE_HOME", str(tmp_path / "eb"))
    monkeypatch.setenv("HINDSIGHT_API_URL", "http://stub")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "stub-key")
    monkeypatch.setenv("HINDSIGHT_BANK_ID", "hermes")
    return tmp_path / "eb"


# ── H1: sink name / accept ────────────────────────────────────

def test_h_h1_sink_name():
    assert HindsightSink(transport=StubTransport()).name == "hindsight"


def test_h_h2_accept_rejects_writeback():
    s = HindsightSink(transport=StubTransport())
    assert s.accept(_evt(source="sink_writeback")) is False
    assert s.accept(_evt()) is True


# ── H3: write() 仅入队 pending，不立即调用 transport ──────────

def test_h_h3_write_enqueues_only(eb_home):
    t = StubTransport()
    s = HindsightSink(transport=t)
    s.write(_evt(eid="x1"))
    assert t.calls == []  # 未触网
    pending = list(s._pending.iter_pending())  # type: ignore[attr-defined]
    assert len(pending) == 1
    assert pending[0].item["event"]["event_id"] == "x1"


# ── H4: flush_pending 成功 → cursor 推进 ──────────────────────

def test_h_h4_flush_success_advances(eb_home):
    t = StubTransport()
    clk = FakeClock()
    s = HindsightSink(transport=t, clock=clk.now)
    for i in range(3):
        s.write(_evt(eid=f"e{i}"))
    n = s.flush_pending()
    assert n == 3
    assert len(t.calls) == 3
    assert list(s._pending.iter_pending()) == []  # type: ignore[attr-defined]


# ── H5: 失败 → 留在 pending + 调度下次重试 ───────────────────

def test_h_h5_failure_keeps_in_pending_with_backoff(eb_home):
    t = StubTransport(always_fail=True)
    clk = FakeClock(t=1000.0)
    s = HindsightSink(transport=t, clock=clk.now,
                      retry_delays=(1.0, 4.0, 16.0))
    s.write(_evt(eid="e1"))
    s.flush_pending()  # attempt 1: fail
    pend = list(s._pending.iter_pending())  # type: ignore[attr-defined]
    assert len(pend) == 1
    # next_retry_at = now + retry_delays[0] = 1001
    assert pend[0].item.get("next_retry_at") == 1001.0
    assert pend[0].item.get("attempts") == 1


# ── H6: 重试调度按 1/4/16 节奏 ────────────────────────────────

def test_h_h6_retry_schedule(eb_home):
    t = StubTransport(always_fail=True)
    clk = FakeClock(t=1000.0)
    s = HindsightSink(transport=t, clock=clk.now,
                      retry_delays=(1.0, 4.0, 16.0))
    s.write(_evt(eid="e1"))

    # 第一次：立即尝试 → 失败 → next=1001
    s.flush_pending()
    # 在 t=1000.5 时尝试：还没到 next_retry_at，跳过
    clk.t = 1000.5
    s.flush_pending()
    assert t.fail_count == 1
    # 在 t=1001 时尝试：到点，再失败 → next=1005
    clk.t = 1001.0
    s.flush_pending()
    assert t.fail_count == 2
    pend = list(s._pending.iter_pending())  # type: ignore[attr-defined]
    assert pend[0].item["next_retry_at"] == 1005.0
    assert pend[0].item["attempts"] == 2


# ── H7: 用尽重试 → 移入 DLQ，pending 移除 ─────────────────────

def test_h_h7_exhausted_goes_to_dlq(eb_home):
    t = StubTransport(always_fail=True)
    clk = FakeClock(t=1000.0)
    s = HindsightSink(transport=t, clock=clk.now,
                      retry_delays=(1.0, 4.0, 16.0),
                      max_attempts=3,
                      circuit_threshold=999)  # 防熔断中断测试
    s.write(_evt(eid="e1"))

    # 3 次失败，分别在 t=1000, 1001, 1005, 然后 t=1021 时达到上限
    s.flush_pending()        # attempt 1 fail → next=1001
    clk.t = 1001.0
    s.flush_pending()        # attempt 2 fail → next=1005
    clk.t = 1005.0
    s.flush_pending()        # attempt 3 fail → 进入 DLQ
    assert t.fail_count == 3
    assert list(s._pending.iter_pending()) == []  # type: ignore[attr-defined]
    dlq_items = list(s._dlq.iter_all())  # type: ignore[attr-defined]
    assert len(dlq_items) == 1
    assert dlq_items[0]["event"]["event_id"] == "e1"
    assert dlq_items[0]["reason"] == "max_attempts_exhausted"


# ── H8: 熔断 60s 内 flush no-op ───────────────────────────────

def test_h_h8_circuit_breaker_pauses_flush(eb_home):
    t = StubTransport(always_fail=True)
    clk = FakeClock(t=1000.0)
    s = HindsightSink(transport=t, clock=clk.now,
                      retry_delays=(1.0,),
                      max_attempts=1,
                      circuit_threshold=2,
                      circuit_cooldown=60.0)
    s.write(_evt(eid="e1"))
    s.write(_evt(eid="e2"))
    s.write(_evt(eid="e3"))
    # 2 次失败 → 熔断
    s.flush_pending()  # 1 次失败 e1 → DLQ
    s.flush_pending()  # 又 1 次失败 e2 → DLQ，触发熔断
    assert s.is_broken() is True
    pre = t.fail_count

    # 熔断期内 flush 完全无 transport 调用
    clk.t = 1030.0  # 仍在 60s 熔断窗口内
    s.flush_pending()
    assert t.fail_count == pre  # transport 未被调用

    # 60s 后恢复
    clk.t = 1100.0
    assert s.is_broken() is False


# ── H9: 成功 → 重置熔断计数 ───────────────────────────────────

def test_h_h9_success_resets_consecutive_failures(eb_home):
    t = StubTransport(fail_times=1)  # 第 1 次失败，之后全成功
    clk = FakeClock(t=1000.0)
    s = HindsightSink(transport=t, clock=clk.now,
                      retry_delays=(1.0, 4.0, 16.0),
                      max_attempts=3,
                      circuit_threshold=3)
    s.write(_evt(eid="e1"))
    s.write(_evt(eid="e2"))

    s.flush_pending()  # e1 fail → next_retry_at=1001
    clk.t = 1001.0
    s.flush_pending()  # e1 success, e2 success
    assert s.consecutive_failures() == 0


# ── H10: HTTP payload 形状 ────────────────────────────────────

def test_h_h10_payload_shape(eb_home):
    t = StubTransport()
    s = HindsightSink(transport=t)
    s.write(_evt(eid="shape1", et="dispatch", profile="regent"))
    s.flush_pending()
    payload = t.calls[0]
    assert payload["bank_id"] == "hermes"  # 来自 env
    assert payload["event_id"] == "shape1"
    assert payload["event_type"] == "dispatch"
    assert payload["profile"] == "regent"
    assert "content" in payload
    assert "timestamp" in payload
