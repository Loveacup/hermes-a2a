"""Event + Sink ABC + consume_for / dispatch_all.

V2 缩窄版: 2 Sink × 16 profile，14 事件类型，无倒排索引.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .cursor import Cursor, CursorStore


@dataclass
class Event:
    raw: dict
    profile: str  # 从 jsonl 路径推导的 owner profile

    @property
    def event_id(self) -> str:
        return self.raw.get("event_id", "") or ""

    @property
    def event_type(self) -> str:
        return self.raw.get("event_type") or self.raw.get("event") or ""

    @property
    def timestamp(self) -> str:
        return self.raw.get("timestamp") or self.raw.get("ts") or ""

    @property
    def task_id(self) -> str:
        tid = self.raw.get("task_id")
        if tid:
            return tid
        body = self.raw.get("content") or self.raw.get("data") or {}
        if isinstance(body, dict):
            return body.get("task_id", "") or ""
        return ""

    @property
    def content(self) -> dict:
        body = self.raw.get("content") or self.raw.get("data") or {}
        return body if isinstance(body, dict) else {}

    @property
    def source(self) -> str:
        return self.raw.get("_source", "") or ""


class Sink(ABC):
    name: str = "sink"

    def accept(self, evt: Event) -> bool:
        # _source=sink_writeback 白名单：防 Sink 自触发产生事件回路
        return evt.source != "sink_writeback"

    @abstractmethod
    def write(self, evt: Event) -> None: ...


def consume_for(sink: Sink, jsonl_path: Path, profile: str) -> int:
    """Tail 单 profile JSONL，喂给 1 个 sink，按 cursor 推进.

    Returns: 实际 accept+write 的事件条数（不含拒绝/损坏）.
    """
    if not jsonl_path.exists():
        return 0

    st = jsonl_path.stat()
    cur = CursorStore.load(sink.name, profile)
    if cur.inode != st.st_ino:
        cur = Cursor(sink=sink.name, profile=profile, inode=st.st_ino)

    written = 0
    with open(jsonl_path, "rb") as f:
        f.seek(cur.byte_offset)
        while True:
            pos = f.tell()
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                f.seek(pos)  # 半行保留到下次
                break
            cur.lineno += 1
            cur.byte_offset = f.tell()
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            evt = Event(raw=d, profile=profile)
            if evt.timestamp:
                cur.last_ts = evt.timestamp
            if sink.accept(evt):
                sink.write(evt)
                written += 1

    CursorStore.save_atomic(cur)
    return written


def dispatch_all(sinks: Iterable[Sink],
                 jsonl_paths: Iterable[Path]) -> dict[str, int]:
    """(sink × profile) 全笛卡尔积一次性 consume，返回 sink/profile→count."""
    counts: dict[str, int] = {}
    paths = list(jsonl_paths)
    for jp in paths:
        profile = jp.parent.name
        for sink in sinks:
            key = f"{sink.name}/{profile}"
            counts[key] = consume_for(sink, jp, profile)
    return counts
