"""死信队列 — append-only JSONL, 无 cursor."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class DLQ:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def put(self, record: dict) -> None:
        rec = dict(record)
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fd = os.open(str(self.path),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def iter_all(self) -> Iterator[dict]:
        if not self.path.exists():
            return iter(())
        return self._iter()

    def _iter(self) -> Iterator[dict]:
        with open(self.path, "rb") as f:
            for raw in f:
                if not raw.endswith(b"\n"):
                    break
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue
