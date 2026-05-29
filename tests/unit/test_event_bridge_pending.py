"""W2 RED: pending.py — 持久化出站队列.

PendingQueue 不变量 (G5):
- enqueue = append + fsync
- dequeue 只推进 cursor，不原地删
- compaction 触发条件: dequeue > 1000 且 file > 10MB
- 半行/损坏行不消费
- 重启后从 cursor 续读
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from event_bridge.pending import PendingItem, PendingQueue  # type: ignore


@pytest.fixture
def queue_path(tmp_path):
    return tmp_path / "pending.jsonl"


# ── P1: enqueue + 持久化 ──────────────────────────────────────

def test_p_p1_enqueue_persists_immediately(queue_path):
    q = PendingQueue(queue_path)
    q.enqueue({"event_id": "p1", "payload": {"x": 1}})
    # 文件存在且非空（fsync 保证）
    assert queue_path.exists()
    assert queue_path.stat().st_size > 0
    # 内容为 JSONL
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_id"] == "p1"


# ── P2: iter_pending 从 cursor 开始 ───────────────────────────

def test_p_p2_iter_pending_starts_at_cursor(queue_path):
    q = PendingQueue(queue_path)
    for i in range(3):
        q.enqueue({"event_id": f"e{i}"})
    items = list(q.iter_pending())
    assert [it.item["event_id"] for it in items] == ["e0", "e1", "e2"]


# ── P3: advance 推进 cursor ───────────────────────────────────

def test_p_p3_advance_moves_cursor(queue_path):
    q = PendingQueue(queue_path)
    for i in range(5):
        q.enqueue({"event_id": f"e{i}"})
    items = list(q.iter_pending())
    q.advance(items[2].line_no)  # 推进过前 3 项
    rest = [it.item["event_id"] for it in q.iter_pending()]
    assert rest == ["e3", "e4"]


# ── P4: 重启后从 cursor 续读 ──────────────────────────────────

def test_p_p4_restart_resumes_from_cursor(queue_path):
    q1 = PendingQueue(queue_path)
    for i in range(4):
        q1.enqueue({"event_id": f"e{i}"})
    items = list(q1.iter_pending())
    q1.advance(items[1].line_no)  # 消费前 2 项

    q2 = PendingQueue(queue_path)  # 新实例（模拟重启）
    rest = [it.item["event_id"] for it in q2.iter_pending()]
    assert rest == ["e2", "e3"]


# ── P5: compaction 重写文件（去掉已 dequeue 段） ──────────────

def test_p_p5_compaction_rewrites_after_threshold(queue_path):
    # 阈值降到测试可达：dequeue > 5 且 file > 100 bytes
    q = PendingQueue(queue_path,
                     compact_dequeue_threshold=5,
                     compact_size_threshold=100)
    for i in range(10):
        q.enqueue({"event_id": f"e{i}", "pad": "x" * 50})
    items = list(q.iter_pending())
    q.advance(items[5].line_no)  # 消费前 6 项 (>5)

    pre = queue_path.stat().st_size
    q.maybe_compact()
    post = queue_path.stat().st_size
    assert post < pre, "compaction 未缩小文件"

    # compaction 后剩余项仍可读
    rest = [it.item["event_id"] for it in q.iter_pending()]
    assert rest == ["e6", "e7", "e8", "e9"]


# ── P6: 损坏行跳过、cursor 仍推进 ─────────────────────────────

def test_p_p6_corrupted_line_skipped(queue_path):
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps({"event_id": "ok1"}) + "\n"
        "{not-json\n"
        + json.dumps({"event_id": "ok2"}) + "\n",
        encoding="utf-8",
    )
    q = PendingQueue(queue_path)
    ids = [it.item["event_id"] for it in q.iter_pending()]
    assert ids == ["ok1", "ok2"]


# ── P7: 半行（无 trailing \n）不消费 ─────────────────────────

def test_p_p7_partial_last_line_held(queue_path):
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps({"event_id": "done"}) + "\n"
        + json.dumps({"event_id": "partial"}),  # 无 \n
        encoding="utf-8",
    )
    q = PendingQueue(queue_path)
    ids = [it.item["event_id"] for it in q.iter_pending()]
    assert ids == ["done"]


# ── P8: empty queue ────────────────────────────────────────────

def test_p_p8_empty_queue_no_items(tmp_path):
    q = PendingQueue(tmp_path / "empty.jsonl")
    assert list(q.iter_pending()) == []


# ── P9: PendingItem 形状 ──────────────────────────────────────

def test_p_p9_pending_item_fields(queue_path):
    q = PendingQueue(queue_path)
    q.enqueue({"event_id": "z"})
    it = next(iter(q.iter_pending()))
    assert isinstance(it, PendingItem)
    assert it.item["event_id"] == "z"
    assert it.line_no >= 1
    assert it.byte_offset > 0
