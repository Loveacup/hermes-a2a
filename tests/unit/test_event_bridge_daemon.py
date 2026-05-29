"""W1 RED: daemon.py — 单 tick 烟测 + plist 模板 XML 合法.

验证点:
- daemon.main 可导入
- tick() 单次执行 → 推进 cursor
- plist 模板存在且为合法 XML 且含 KeepAlive / RunAtLoad
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from event_bridge import daemon  # type: ignore
from event_bridge.sinks.obsidian import ObsidianSink  # type: ignore
from event_bridge.cursor import CursorStore  # type: ignore


@pytest.fixture
def eb_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    eb = tmp_path / "event-bridge"
    vault = tmp_path / "vault"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("EVENT_BRIDGE_HOME", str(eb))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    (home / "profiles").mkdir(parents=True)
    return home, eb, vault


def _mk_event(event_id: str, ts: str = "2026-05-30T00:00:00Z") -> dict:
    return {
        "event_id": event_id,
        "event_type": "edict",
        "timestamp": ts,
        "profile": "regent",
        "content": {"content": "hi", "issuer": "regent"},
        "run_id": "r1",
    }


# ── D1: daemon module 可导入 + 有 main() / tick() ─────────────

def test_d_d1_daemon_module_shape():
    assert hasattr(daemon, "main")
    assert hasattr(daemon, "tick")
    assert callable(daemon.tick)


# ── D2: 单 tick 推进 cursor 并触发 Obsidian 写入 ──────────────

def test_d_d2_single_tick_writes_obsidian(eb_env):
    home, eb, vault = eb_env
    d = home / "profiles" / "regent"
    d.mkdir(parents=True)
    jsonl = d / "empire-thread.jsonl"
    jsonl.write_text(json.dumps(_mk_event("tick1")) + "\n", encoding="utf-8")

    counts = daemon.tick([ObsidianSink()])
    assert counts.get("obsidian/regent") == 1

    target = vault / "88_event-bridge" / "2026" / "05" / "30" / "tick1.md"
    assert target.exists()

    cur = CursorStore.load("obsidian", "regent")
    assert cur.lineno == 1


# ── D3: 第二次 tick 增量为 0 ───────────────────────────────────

def test_d_d3_second_tick_idempotent(eb_env):
    home, _, _ = eb_env
    d = home / "profiles" / "regent"
    d.mkdir(parents=True)
    (d / "empire-thread.jsonl").write_text(
        json.dumps(_mk_event("once")) + "\n", encoding="utf-8")

    sinks = [ObsidianSink()]
    daemon.tick(sinks)
    counts2 = daemon.tick(sinks)
    assert counts2.get("obsidian/regent", 0) == 0


# ── D4: plist 模板存在且 XML 合法 ──────────────────────────────

def test_d_d4_plist_template_valid_xml():
    repo_root = Path(__file__).resolve().parents[2]
    plist = repo_root / "core" / "templates" / "event-bridge-launchd.plist"
    assert plist.exists(), f"plist 模板缺失: {plist}"
    # 替换占位符后应为合法 XML
    text = plist.read_text(encoding="utf-8")
    text = (text.replace("{{PYTHON}}", "/usr/bin/python3")
                .replace("{{WORKING_DIR}}", "/tmp")
                .replace("{{HOME}}", "/Users/alexcai")
                .replace("{{HERMES_HOME}}", "/Users/alexcai/.hermes")
                .replace("{{OBSIDIAN_VAULT}}", "/Users/alexcai/Documents/Obsidian/AlexCai")
                .replace("{{LOG_DIR}}", "/Users/alexcai/.hermes/logs"))
    root = ET.fromstring(text)
    assert root.tag == "plist"
    # 含 KeepAlive=true 与 RunAtLoad=true
    keys = [k.text for k in root.iter("key")]
    assert "KeepAlive" in keys
    assert "RunAtLoad" in keys
    assert "Label" in keys
