"""W2 RED: dlq.py — 死信队列（append-only, 无 cursor）."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from event_bridge.dlq import DLQ  # type: ignore


@pytest.fixture
def dlq_path(tmp_path):
    return tmp_path / "dlq.jsonl"


# ── DLQ1: append + 持久化 ─────────────────────────────────────

def test_dlq_d1_append_persists(dlq_path):
    dlq = DLQ(dlq_path)
    dlq.put({"event_id": "fail1", "reason": "exhausted"})
    lines = dlq_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event_id"] == "fail1"
    assert rec["reason"] == "exhausted"
    assert "ts" in rec  # 自动注入时间戳


# ── DLQ2: 多次插入保持顺序 ────────────────────────────────────

def test_dlq_d2_preserves_order(dlq_path):
    dlq = DLQ(dlq_path)
    for i in range(3):
        dlq.put({"event_id": f"e{i}"})
    items = list(dlq.iter_all())
    assert [it["event_id"] for it in items] == ["e0", "e1", "e2"]


# ── DLQ3: 空 DLQ ──────────────────────────────────────────────

def test_dlq_d3_empty_iter(tmp_path):
    dlq = DLQ(tmp_path / "missing.jsonl")
    assert list(dlq.iter_all()) == []


# ── DLQ4: 损坏行跳过 ──────────────────────────────────────────

def test_dlq_d4_corrupted_skipped(dlq_path):
    dlq_path.parent.mkdir(parents=True, exist_ok=True)
    dlq_path.write_text(
        json.dumps({"event_id": "ok"}) + "\n"
        "{not-json\n",
        encoding="utf-8",
    )
    items = list(DLQ(dlq_path).iter_all())
    assert [it["event_id"] for it in items] == ["ok"]
