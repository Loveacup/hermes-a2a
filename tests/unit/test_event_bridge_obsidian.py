"""W1 RED: sinks/obsidian.py — Markdown 模板渲染 + vault 幂等写入.

验证点:
- 按 timestamp 拆分到 YYYY/MM/DD 子目录
- frontmatter 含 event_id / event_type / profile / timestamp / task_id?
- 幂等：相同 event_id 重复写入不覆盖
- 无 timestamp → unknown/ 兜底
- _source=sink_writeback → 拒绝（accept 返回 False，防回路）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from event_bridge.core import Event  # type: ignore
from event_bridge.sinks.obsidian import ObsidianSink  # type: ignore


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    monkeypatch.setenv("OBSIDIAN_VAULT", str(v))
    return v


def _evt(event_id="ev0001", event_type="edict", ts="2026-05-30T12:34:56Z",
         profile="regent", content=None, source=""):
    raw = {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": ts,
        "content": content or {"note": "hello"},
        "run_id": "r1",
    }
    if source:
        raw["_source"] = source
    return Event(raw=raw, profile=profile)


# ── O1: 路径布局 ───────────────────────────────────────────────

def test_o_o1_path_layout_by_date(vault):
    s = ObsidianSink()
    s.write(_evt(event_id="abc123", ts="2026-05-30T10:00:00Z"))
    target = vault / "88_event-bridge" / "2026" / "05" / "30" / "abc123.md"
    assert target.exists()


# ── O2: frontmatter 字段完整 ───────────────────────────────────

def test_o_o2_frontmatter_fields(vault):
    s = ObsidianSink()
    s.write(_evt(event_id="ev1", event_type="dispatch",
                 ts="2026-05-30T08:00:00Z", profile="engineer",
                 content={"target": "default", "command": "ls",
                          "task_id": "t_abc"}))
    f = vault / "88_event-bridge" / "2026" / "05" / "30" / "ev1.md"
    txt = f.read_text(encoding="utf-8")
    assert "event_id: ev1" in txt
    assert "event_type: dispatch" in txt
    assert "profile: engineer" in txt
    assert "timestamp: 2026-05-30T08:00:00Z" in txt
    assert "task_id: t_abc" in txt


# ── O3: 幂等写入 ───────────────────────────────────────────────

def test_o_o3_idempotent_write_same_event_id(vault):
    s = ObsidianSink()
    evt = _evt(event_id="dup1", content={"note": "v1"})
    s.write(evt)
    # 改 content 再写，文件不应被覆盖
    evt2 = _evt(event_id="dup1", content={"note": "v2"})
    s.write(evt2)
    f = vault / "88_event-bridge" / "2026" / "05" / "30" / "dup1.md"
    txt = f.read_text(encoding="utf-8")
    assert "v1" in txt
    assert "v2" not in txt


# ── O4: 无 timestamp → unknown 兜底 ────────────────────────────

def test_o_o4_missing_ts_falls_back(vault):
    s = ObsidianSink()
    s.write(_evt(event_id="nots", ts=""))
    # 不应崩溃，应落到 unknown/
    target_root = vault / "88_event-bridge" / "unknown"
    assert target_root.exists()


# ── O5: accept 拒绝 _source=sink_writeback（防回路） ──────────

def test_o_o5_accept_rejects_sink_writeback():
    s = ObsidianSink()
    evt = _evt(source="sink_writeback")
    assert s.accept(evt) is False
    normal = _evt()
    assert s.accept(normal) is True


# ── O6: sink name 常量 ─────────────────────────────────────────

def test_o_o6_sink_name():
    s = ObsidianSink()
    assert s.name == "obsidian"
