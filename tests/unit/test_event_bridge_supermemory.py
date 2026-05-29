"""SupermemorySink — 长期记忆 sink 单元测试.

注入 StubTransport，验证:
- sink name / accept 行为
- container_tag 从 profile_map 解析（regent → hermes-cabinet, default → hermes, 未知 → fallback hermes）
- write() 直发 POST 不入队
- payload 形状（content / containerTags / customId / metadata）
- 失败被吞掉，不抛异常（best effort 语义）
"""
from __future__ import annotations

import pytest

from event_bridge.core import Event  # type: ignore
from event_bridge.sinks.supermemory import (  # type: ignore
    SupermemorySink,
    TransportError,
)


# ── 注入式 Transport ──────────────────────────────────────────

class StubTransport:
    def __init__(self, *, always_fail: bool = False):
        self.always_fail = always_fail
        self.calls: list[dict] = []

    def add_document(self, payload: dict) -> dict:
        self.calls.append(payload)
        if self.always_fail:
            raise TransportError("simulated")
        return {"ok": True, "id": f"doc{len(self.calls)}"}


# ── 工具 ──────────────────────────────────────────────────────

def _evt(eid="ev1", ts="2026-05-30T00:00:00Z",
         et="edict", profile="regent", source="", task_id=""):
    raw: dict = {
        "event_id": eid,
        "event_type": et,
        "timestamp": ts,
        "content": {"content": "hi", "issuer": profile},
        "run_id": "r1",
    }
    if task_id:
        raw["task_id"] = task_id
    if source:
        raw["_source"] = source
    return Event(raw=raw, profile=profile)


@pytest.fixture
def profile_map():
    return {
        "regent": "hermes-cabinet",
        "default": "hermes",
    }


# ── S1: sink name ────────────────────────────────────────────

def test_s_s1_sink_name(profile_map):
    s = SupermemorySink(transport=StubTransport(), profile_map=profile_map)
    assert s.name == "supermemory"


# ── S2: accept 拒绝 sink_writeback ───────────────────────────

def test_s_s2_accept_rejects_writeback(profile_map):
    s = SupermemorySink(transport=StubTransport(), profile_map=profile_map)
    assert s.accept(_evt(source="sink_writeback")) is False
    assert s.accept(_evt()) is True


# ── S3: write() 直发 transport，不入队 ───────────────────────

def test_s_s3_write_calls_transport(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(eid="x1"))
    assert len(t.calls) == 1
    assert t.calls[0]["customId"] == "x1"


# ── S4: container_tag 映射（regent → hermes-cabinet） ────────

def test_s_s4_container_tag_regent(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(profile="regent"))
    assert t.calls[0]["containerTags"] == ["hermes-cabinet"]


# ── S5: container_tag 映射（default → hermes） ───────────────

def test_s_s5_container_tag_default(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(profile="default"))
    assert t.calls[0]["containerTags"] == ["hermes"]


# ── S6: container_tag fallback（未知 profile → hermes） ──────

def test_s_s6_container_tag_fallback(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(profile="engineer"))
    assert t.calls[0]["containerTags"] == ["hermes"]


# ── S7: payload 形状（content / metadata / customId） ───────

def test_s_s7_payload_shape(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(eid="shape1", et="dispatch", profile="regent",
                 task_id="t-123"))
    p = t.calls[0]
    assert "content" in p
    assert "# DISPATCH" in p["content"]
    assert p["containerTags"] == ["hermes-cabinet"]
    assert p["customId"] == "shape1"
    assert p["metadata"]["event_type"] == "dispatch"
    assert p["metadata"]["profile"] == "regent"
    assert p["metadata"]["task_id"] == "t-123"
    assert "timestamp" in p["metadata"]


# ── S8: 失败被吞，不抛 ──────────────────────────────────────

def test_s_s8_transport_failure_is_swallowed(profile_map):
    t = StubTransport(always_fail=True)
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(eid="fail1"))  # 不应抛
    assert len(t.calls) == 1


# ── S9: 无 task_id 时 metadata 中不带 task_id ───────────────

def test_s_s9_metadata_without_task_id(profile_map):
    t = StubTransport()
    s = SupermemorySink(transport=t, profile_map=profile_map)
    s.write(_evt(eid="notask"))
    assert "task_id" not in t.calls[0]["metadata"]
