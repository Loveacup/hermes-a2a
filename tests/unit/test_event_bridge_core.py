"""W1 RED: core.py — Event 包装 + 16-profile JSONL 扫描 + consume_for 分派.

验证点:
- jsonl_paths_for_all_profiles: 多 profile 自动发现
- Event 字段访问（兼容 event_type/event / content/data 双 key）
- consume_for: 推进 cursor、跳过损坏行、半行不消费
- dispatch_all: 每 (sink × profile) 独立 cursor
- inode 旋转 → 冷启
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from event_bridge.core import Event, Sink, consume_for, dispatch_all  # type: ignore
from event_bridge.cursor import CursorStore  # type: ignore
from event_bridge.paths import jsonl_paths_for_all_profiles  # type: ignore


# ── 测试 Sink ──────────────────────────────────────────────────

class FakeSink(Sink):
    def __init__(self, name="fake", accept_all=True):
        self.name = name
        self._accept_all = accept_all
        self.written: list = []

    def accept(self, evt: Event) -> bool:
        if evt.source == "sink_writeback":
            return False
        return self._accept_all

    def write(self, evt: Event) -> None:
        self.written.append(evt)


# ── 工具 ───────────────────────────────────────────────────────

def _mk_event(event_id: str, ts: str = "2026-05-30T00:00:00Z",
              event_type: str = "edict") -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": ts,
        "profile": "regent",
        "content": {"note": event_id},
        "run_id": "r1",
    }


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


@pytest.fixture
def eb_env(tmp_path, monkeypatch):
    """隔离 HERMES_HOME + EVENT_BRIDGE_HOME 到 tmp_path."""
    home = tmp_path / "hermes"
    eb = tmp_path / "event-bridge"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("EVENT_BRIDGE_HOME", str(eb))
    (home / "profiles").mkdir(parents=True)
    return home


# ── E1: Event 字段访问 ─────────────────────────────────────────

def test_e_e1_event_legacy_keys():
    raw = {"event_id": "x1", "event": "edict", "ts": "2026-05-30T00:00:00Z",
           "data": {"foo": 1}, "_source": "sink_writeback"}
    e = Event(raw=raw, profile="regent")
    assert e.event_id == "x1"
    assert e.event_type == "edict"
    assert e.timestamp == "2026-05-30T00:00:00Z"
    assert e.content == {"foo": 1}
    assert e.source == "sink_writeback"


def test_e_e2_event_new_keys():
    raw = {"event_id": "x2", "event_type": "dispatch",
           "timestamp": "2026-05-30T00:00:01Z",
           "content": {"target": "default", "command": "ls",
                       "task_id": "t_z"}}
    e = Event(raw=raw, profile="default")
    assert e.event_type == "dispatch"
    assert e.task_id == "t_z"


# ── E3: 16-profile 自动发现 ────────────────────────────────────

def test_e_e3_scan_profiles_discovers_jsonl(eb_env):
    home = eb_env
    for name in ("regent", "default", "engineer"):
        d = home / "profiles" / name
        d.mkdir(parents=True)
        (d / "empire-thread.jsonl").write_text("", encoding="utf-8")
    (home / "profiles" / "no-thread").mkdir(parents=True)  # 无 jsonl 跳过
    paths = jsonl_paths_for_all_profiles()
    profile_names = sorted(p.parent.name for p in paths)
    assert profile_names == ["default", "engineer", "regent"]


# ── E4: consume_for 推进 cursor ─────────────────────────────────

def test_e_e4_consume_advances_cursor(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    events = [_mk_event(f"e{i}") for i in range(3)]
    _write_jsonl(jsonl, events)

    sink = FakeSink()
    n = consume_for(sink, jsonl, "regent")
    assert n == 3
    assert len(sink.written) == 3

    cur = CursorStore.load("fake", "regent")
    assert cur.lineno == 3
    assert cur.byte_offset == os.path.getsize(jsonl)


# ── E5: consume_for 增量（第二次跑只看新增） ───────────────────

def test_e_e5_consume_incremental(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    _write_jsonl(jsonl, [_mk_event("a"), _mk_event("b")])
    sink = FakeSink()
    assert consume_for(sink, jsonl, "regent") == 2

    # append 新事件
    _write_jsonl(jsonl, [_mk_event("c")])
    assert consume_for(sink, jsonl, "regent") == 1
    assert [e.event_id for e in sink.written] == ["a", "b", "c"]


# ── E6: 损坏行跳过 ─────────────────────────────────────────────

def test_e_e6_corrupted_line_skipped(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text(
        json.dumps(_mk_event("good1")) + "\n"
        "{not-json\n"
        + json.dumps(_mk_event("good2")) + "\n",
        encoding="utf-8",
    )
    sink = FakeSink()
    assert consume_for(sink, jsonl, "regent") == 2
    assert [e.event_id for e in sink.written] == ["good1", "good2"]
    cur = CursorStore.load("fake", "regent")
    assert cur.lineno == 3  # 含损坏行


# ── E7: 半行（无 \n）不消费 ────────────────────────────────────

def test_e_e7_partial_last_line_held(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(_mk_event("done")) + "\n"
    # 第二行无 \n
    body += json.dumps(_mk_event("partial"))
    jsonl.write_text(body, encoding="utf-8")
    sink = FakeSink()
    assert consume_for(sink, jsonl, "regent") == 1
    assert [e.event_id for e in sink.written] == ["done"]
    # 下次再补全
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write("\n")
    assert consume_for(sink, jsonl, "regent") == 1
    assert [e.event_id for e in sink.written] == ["done", "partial"]


# ── E8: inode 旋转 → 冷启 ──────────────────────────────────────

def test_e_e8_inode_rotation_resets_cursor(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    _write_jsonl(jsonl, [_mk_event("old1"), _mk_event("old2")])
    sink = FakeSink()
    assert consume_for(sink, jsonl, "regent") == 2

    # 旋转：删后重建（新 inode）
    jsonl.unlink()
    _write_jsonl(jsonl, [_mk_event("new1")])

    sink2 = FakeSink()
    assert consume_for(sink2, jsonl, "regent") == 1
    assert [e.event_id for e in sink2.written] == ["new1"]


# ── E9: dispatch_all per (sink × profile) 独立 ─────────────────

def test_e_e9_dispatch_independent_cursors(eb_env):
    for prof in ("regent", "default"):
        d = eb_env / "profiles" / prof
        d.mkdir(parents=True)
        _write_jsonl(d / "empire-thread.jsonl",
                     [_mk_event(f"{prof}_e1"), _mk_event(f"{prof}_e2")])

    sa = FakeSink(name="sa")
    sb = FakeSink(name="sb")
    counts = dispatch_all([sa, sb], jsonl_paths_for_all_profiles())
    # 4 个 (sink × profile) 组合，每个看到 2 条
    assert counts == {
        "sa/default": 2, "sa/regent": 2,
        "sb/default": 2, "sb/regent": 2,
    }
    # 各 cursor 都到了 lineno=2
    for sink in ("sa", "sb"):
        for prof in ("regent", "default"):
            assert CursorStore.load(sink, prof).lineno == 2


# ── E10: accept=False 不写出但 cursor 仍推进 ──────────────────

def test_e_e10_rejected_events_still_advance_cursor(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    _write_jsonl(jsonl, [_mk_event(f"e{i}") for i in range(5)])

    sink = FakeSink(accept_all=False)
    n = consume_for(sink, jsonl, "regent")
    assert n == 0
    assert sink.written == []
    cur = CursorStore.load("fake", "regent")
    assert cur.lineno == 5
