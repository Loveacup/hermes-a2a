"""Per-(sink, profile) 增量消费状态.

崩溃安全: 写入走 tmp + os.replace 原子化.
inode 字段允许检测 logrotate / 截断 / 重建 → 冷启.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from .paths import cursors_dir


@dataclass
class Cursor:
    sink: str
    profile: str
    lineno: int = 0
    byte_offset: int = 0
    inode: int = 0
    last_ts: str = ""


def _cursor_path(sink: str, profile: str) -> Path:
    return cursors_dir() / f"{sink}__{profile}.json"


class CursorStore:
    @staticmethod
    def load(sink: str, profile: str) -> Cursor:
        path = _cursor_path(sink, profile)
        if not path.exists():
            return Cursor(sink=sink, profile=profile)
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return Cursor(**d)
        except (json.JSONDecodeError, TypeError, ValueError):
            return Cursor(sink=sink, profile=profile)

    @staticmethod
    def save_atomic(cursor: Cursor) -> None:
        path = _cursor_path(cursor.sink, cursor.profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(asdict(cursor), ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
