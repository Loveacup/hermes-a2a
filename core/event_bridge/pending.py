"""持久化出站队列 (G5 不变量).

- enqueue = O_APPEND + fsync
- iter_pending = 从 cursor 顺序读
- advance = 仅推进 cursor，不原地删
- compaction = dequeue > N 且 file > S bytes 时 rewrite
- 半行/损坏行跳过，不破坏队列
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class PendingItem:
    line_no: int
    byte_offset: int  # 行尾 offset（cursor 应推进到此处）
    item: dict


class PendingQueue:
    def __init__(self, path: Path,
                 compact_dequeue_threshold: int = 1000,
                 compact_size_threshold: int = 10 * 1024 * 1024):
        self.path = Path(path)
        self.cursor_path = self.path.with_suffix(self.path.suffix + ".cursor")
        self.compact_dequeue_threshold = compact_dequeue_threshold
        self.compact_size_threshold = compact_size_threshold
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── 状态 ──────────────────────────────────────────────────

    def _read_cursor(self) -> tuple[int, int]:
        """returns (line_no, byte_offset)."""
        if not self.cursor_path.exists():
            return (0, 0)
        try:
            d = json.loads(self.cursor_path.read_text(encoding="utf-8"))
            return (int(d.get("line_no", 0)), int(d.get("byte_offset", 0)))
        except (json.JSONDecodeError, ValueError):
            return (0, 0)

    def _write_cursor(self, line_no: int, byte_offset: int) -> None:
        tmp = self.cursor_path.with_suffix(self.cursor_path.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "line_no": line_no, "byte_offset": byte_offset,
        }), encoding="utf-8")
        os.replace(tmp, self.cursor_path)

    # ── enqueue ───────────────────────────────────────────────

    def enqueue(self, item: dict) -> None:
        # 若文件末尾不是 \n，先补一个，防 torn-line 拼接
        if self.path.exists() and self.path.stat().st_size > 0:
            with open(self.path, "rb") as r:
                r.seek(-1, os.SEEK_END)
                last = r.read(1)
            if last != b"\n":
                with open(self.path, "ab") as a:
                    a.write(b"\n")
                    a.flush()
                    os.fsync(a.fileno())
        line = json.dumps(item, ensure_ascii=False) + "\n"
        fd = os.open(str(self.path),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    # ── iter_pending ──────────────────────────────────────────

    def iter_pending(self) -> Iterator[PendingItem]:
        if not self.path.exists():
            return iter(())
        start_line, start_byte = self._read_cursor()
        return self._iter_from(start_line, start_byte)

    def _iter_from(self, start_line: int,
                   start_byte: int) -> Iterator[PendingItem]:
        with open(self.path, "rb") as f:
            f.seek(start_byte)
            lineno = start_line
            while True:
                pos = f.tell()
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break  # 半行保留
                lineno += 1
                end_offset = f.tell()
                try:
                    d = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                yield PendingItem(line_no=lineno, byte_offset=end_offset,
                                  item=d)

    # ── advance ───────────────────────────────────────────────

    def advance(self, line_no: int) -> None:
        # 找到该 line_no 对应的 byte_offset：重新扫一遍简单稳妥
        with open(self.path, "rb") as f:
            cur_line = 0
            byte_off = 0
            while True:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                cur_line += 1
                byte_off = f.tell()
                if cur_line == line_no:
                    break
        self._write_cursor(cur_line, byte_off)

    # ── compaction ────────────────────────────────────────────

    def maybe_compact(self) -> None:
        if not self.path.exists():
            return
        dequeued_lines, byte_off = self._read_cursor()
        size = self.path.stat().st_size
        if (dequeued_lines <= self.compact_dequeue_threshold
                or size <= self.compact_size_threshold):
            return
        # 重写：从 byte_off 之后的内容拷到新文件，原子替换
        tmp = self.path.with_suffix(self.path.suffix + ".compact")
        with open(self.path, "rb") as src, open(tmp, "wb") as dst:
            src.seek(byte_off)
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, self.path)
        # 重置 cursor 至 0
        self._write_cursor(0, 0)
