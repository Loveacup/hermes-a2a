"""Obsidian Sink: Markdown ADR / 事件日志写入 vault.

布局: <vault>/88_event-bridge/YYYY/MM/DD/<event_id>.md
幂等: target.exists() → skip.
"""
from __future__ import annotations

import json

from ..core import Event, Sink
from ..paths import obsidian_event_dir


class ObsidianSink(Sink):
    name = "obsidian"

    def write(self, evt: Event) -> None:
        ts = evt.timestamp
        if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
            year, month, day = ts[0:4], ts[5:7], ts[8:10]
        else:
            year, month, day = "unknown", "unknown", "unknown"
        target_dir = obsidian_event_dir() / year / month / day
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{evt.event_id or 'noid'}.md"
        if target.exists():
            return  # 幂等
        target.write_text(_render(evt), encoding="utf-8")


def _render(evt: Event) -> str:
    et = evt.event_type or "unknown"
    fm = [
        "---",
        f"event_id: {evt.event_id}",
        f"event_type: {et}",
        f"profile: {evt.profile}",
        f"timestamp: {evt.timestamp}",
    ]
    if evt.task_id:
        fm.append(f"task_id: {evt.task_id}")
    fm.append("---")
    body = [
        "",
        f"# {et.upper()} — {evt.timestamp}",
        "",
        "## Content",
        "",
        "```json",
        json.dumps(evt.content, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(fm + body) + "\n"
