"""W2 RED: at-least-once 投递 + 崩溃语义合同测试.

不可变约定 (G5):
1. consume_for 内 sink.write 抛异常 → cursor 不持久化 → 重启后该事件被再次读取
2. PendingQueue.enqueue 完成且 fsync 后，即使 daemon kill -9，
   重启仍能读到该 pending 项
3. dequeue advance 必须在 sink.write 成功之后才能推进
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from event_bridge.core import Event, Sink, consume_for  # type: ignore
from event_bridge.cursor import CursorStore  # type: ignore
from event_bridge.pending import PendingQueue  # type: ignore


class RaisingSink(Sink):
    name = "raising"

    def __init__(self, raise_after: int = 1):
        self.raise_after = raise_after
        self.calls = 0

    def write(self, evt: Event) -> None:
        self.calls += 1
        if self.calls > self.raise_after:
            raise RuntimeError("simulated mid-batch crash")


class CountingSink(Sink):
    name = "counting"

    def __init__(self):
        self.calls = 0

    def write(self, evt: Event) -> None:
        self.calls += 1


@pytest.fixture
def eb_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    eb = tmp_path / "eb"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("EVENT_BRIDGE_HOME", str(eb))
    (home / "profiles" / "regent").mkdir(parents=True)
    return home


def _mk_event(eid: str) -> dict:
    return {
        "event_id": eid,
        "event_type": "edict",
        "timestamp": "2026-05-30T00:00:00Z",
        "profile": "regent",
        "content": {"content": "x", "issuer": "regent"},
        "run_id": "r1",
    }


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ── C1: sink.write 抛错 → cursor 不推进 ──────────────────────

def test_c_c1_mid_batch_crash_does_not_advance_cursor(eb_env):
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    _write_jsonl(jsonl, [_mk_event(f"e{i}") for i in range(5)])

    sink = RaisingSink(raise_after=2)
    with pytest.raises(RuntimeError):
        consume_for(sink, jsonl, "regent")

    # cursor 应未持久化（仍是初始零状态）
    cur = CursorStore.load("raising", "regent")
    assert cur.lineno == 0
    assert cur.byte_offset == 0

    # 重启用新 sink 重读 → 全部 5 个事件再来一遍
    s2 = CountingSink()
    n = consume_for(s2, jsonl, "regent")
    assert n == 5


# ── C2: PendingQueue.enqueue 后即可被新实例读到 ──────────────

def test_c_c2_pending_durable_after_enqueue(tmp_path):
    p = tmp_path / "pending.jsonl"
    q1 = PendingQueue(p)
    q1.enqueue({"event_id": "must_survive"})
    # 模拟 kill -9: 不调用任何 close/flush（已在 enqueue 内 fsync）
    del q1
    q2 = PendingQueue(p)
    items = list(q2.iter_pending())
    assert [it.item["event_id"] for it in items] == ["must_survive"]


# ── C3: cursor 推进的"at-least-once"边界 ─────────────────────

def test_c_c3_at_least_once_boundary(eb_env):
    """consume_for 正常返回 → cursor 持久化.
    重启后再 consume → 0 增量.
    """
    jsonl = eb_env / "profiles" / "regent" / "empire-thread.jsonl"
    _write_jsonl(jsonl, [_mk_event(f"e{i}") for i in range(3)])
    s1 = CountingSink()
    assert consume_for(s1, jsonl, "regent") == 3

    s2 = CountingSink()
    # 第二次 consume：cursor 已持久化，0 增量
    assert consume_for(s2, jsonl, "regent") == 0


# ── C4: PendingQueue 半行写入崩溃后下次读取忽略 ──────────────

def test_c_c4_partial_enqueue_held_until_complete(tmp_path):
    """模拟：daemon 写一半 newline 之前崩溃 → 下次启动时半行不消费."""
    p = tmp_path / "pending.jsonl"
    # 手工写入「完整一行 + 半行」
    p.write_text(
        json.dumps({"event_id": "done"}) + "\n"
        + json.dumps({"event_id": "torn"}),
        encoding="utf-8",
    )
    q = PendingQueue(p)
    ids = [it.item["event_id"] for it in q.iter_pending()]
    assert ids == ["done"]
    # 之后 enqueue 不会破坏现有内容（拼到尾部）
    q.enqueue({"event_id": "fresh"})
    ids2 = [it.item["event_id"] for it in q.iter_pending()]
    # torn 半行此时仍在文件里但不可读 — 它会"吃掉"下次 enqueue 的开头
    # 实现要求：enqueue 时如果文件不以 \n 结尾，应先补一个 \n
    assert "done" in ids2
    assert "fresh" in ids2
