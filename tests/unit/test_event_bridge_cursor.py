"""W1 RED: cursor.py — per-(sink, profile) 增量消费状态.

验证点:
- 初始 cursor 为零状态
- byte_offset / lineno 推进
- inode 旋转 → 冷启
- 原子保存（崩溃中点不留半文件）
- 半行（无 trailing \\n）不消费

EmpireThread JSONL 行格式参考 ~/.hermes/profiles/regent/scripts/empire_thread.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# Module under test (导入失败即 RED)
from event_bridge.cursor import Cursor, CursorStore  # type: ignore


@pytest.fixture
def eb_home(tmp_path, monkeypatch):
    """隔离 EVENT_BRIDGE_HOME 到 tmp_path."""
    monkeypatch.setenv("EVENT_BRIDGE_HOME", str(tmp_path / "event-bridge"))
    return tmp_path / "event-bridge"


# ── U1: Cursor 数据形状 ─────────────────────────────────────────

def test_c_u1_cursor_defaults():
    c = Cursor(sink="obsidian", profile="regent")
    assert c.sink == "obsidian"
    assert c.profile == "regent"
    assert c.lineno == 0
    assert c.byte_offset == 0
    assert c.inode == 0
    assert c.last_ts == ""


# ── U2: load() 缺省 ─────────────────────────────────────────────

def test_c_u2_load_missing_returns_zero_cursor(eb_home):
    c = CursorStore.load("obsidian", "regent")
    assert c.sink == "obsidian"
    assert c.profile == "regent"
    assert c.byte_offset == 0
    assert c.lineno == 0


# ── U3: save → load 往返 ────────────────────────────────────────

def test_c_u3_save_then_load_roundtrip(eb_home):
    c = Cursor(sink="hindsight", profile="default",
               lineno=42, byte_offset=8192, inode=99887766,
               last_ts="2026-05-30T00:00:00Z")
    CursorStore.save_atomic(c)
    loaded = CursorStore.load("hindsight", "default")
    assert loaded == c


# ── U4: 原子写入（tmp 旁路） ────────────────────────────────────

def test_c_u4_save_atomic_no_stray_tmp(eb_home):
    c = Cursor(sink="obsidian", profile="x", lineno=1, byte_offset=10, inode=1)
    CursorStore.save_atomic(c)
    cursors_dir = eb_home / "cursors"
    files = sorted(p.name for p in cursors_dir.iterdir())
    assert files == ["obsidian__x.json"]  # 无 .tmp 残留


# ── U5: 多 sink / 多 profile 独立 ──────────────────────────────

def test_c_u5_independent_storage(eb_home):
    CursorStore.save_atomic(Cursor(sink="obsidian", profile="regent",
                                   lineno=10, byte_offset=100, inode=1))
    CursorStore.save_atomic(Cursor(sink="hindsight", profile="regent",
                                   lineno=20, byte_offset=200, inode=1))
    CursorStore.save_atomic(Cursor(sink="obsidian", profile="default",
                                   lineno=30, byte_offset=300, inode=2))

    a = CursorStore.load("obsidian", "regent")
    b = CursorStore.load("hindsight", "regent")
    c = CursorStore.load("obsidian", "default")
    assert (a.lineno, b.lineno, c.lineno) == (10, 20, 30)


# ── U6: 损坏 JSON → 零状态降级 ─────────────────────────────────

def test_c_u6_corrupted_cursor_falls_back_to_zero(eb_home):
    # 先合法保存一次以 mkdir
    CursorStore.save_atomic(Cursor(sink="obsidian", profile="regent",
                                   lineno=5, byte_offset=50, inode=1))
    # 写坏
    f = eb_home / "cursors" / "obsidian__regent.json"
    f.write_text("not-json", encoding="utf-8")
    loaded = CursorStore.load("obsidian", "regent")
    assert loaded.lineno == 0
    assert loaded.byte_offset == 0
