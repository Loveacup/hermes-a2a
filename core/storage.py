"""
SQLite-backed task storage for hermes-a2a.

Replaces the in-memory ``_tasks: dict`` previously used in ``core/server.py``,
addressing P0-5 (task state lost on process restart) and providing thread-safe
access for the upcoming ThreadingHTTPServer upgrade.

Design notes:
- Single-process scope: each Hermes profile runs its own process, so we do not
  need cross-process locking — but we *do* need thread safety because the HTTP
  server is multi-threaded.
- WAL mode is enabled to allow concurrent readers alongside a single writer.
- All dict-valued fields (message, history, artifact) are JSON-serialised with
  ``ensure_ascii=False`` so that non-ASCII content (e.g., Chinese) is stored
  legibly in the database.
- Only Python stdlib is used (sqlite3, json, threading, datetime, pathlib).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  context_id TEXT,
  message_json TEXT,
  history_json TEXT,
  semantic_status TEXT,
  completion_reason TEXT,
  artifact_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string (sortable lexicographically)."""
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str | None:
    """JSON-serialise ``value`` (non-ASCII preserved). ``None`` passes through."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    """JSON-deserialise ``value``. ``None`` passes through; bare strings tolerated."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class TaskStore:
    """SQLite-backed task storage. Thread-safe via internal lock.

    A single ``sqlite3.Connection`` is shared across threads with
    ``check_same_thread=False``; all access is serialised by an internal
    ``threading.Lock``. This is simple and correct for the moderate write rate
    expected here (a handful of A2A tasks per second at most).
    """

    def __init__(
        self,
        db_path: str | Path,
        max_tasks: int = 1000,
        ttl_seconds: int = 3600,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_tasks = max_tasks
        self.ttl_seconds = ttl_seconds

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; PRAGMA + DDL + single-statement DML
        )
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ #
    # Row <-> dict helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "status": row["status"],
            "context_id": row["context_id"],
            "message": _loads(row["message_json"]),
            "history": _loads(row["history_json"]) or [],
            "semantic_status": row["semantic_status"],
            "completion_reason": row["completion_reason"],
            "artifact": _loads(row["artifact_json"]),
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def save(self, task: dict[str, Any]) -> None:
        """Insert or replace a task. ``task['id']`` is required.

        Existing ``created_at`` is preserved if already present; otherwise a
        fresh UTC timestamp is generated. ``updated_at`` is always refreshed.
        """
        task_id = task.get("id")
        if not task_id:
            raise ValueError("task['id'] is required")

        now = _utcnow_iso()
        created_at = task.get("created_at") or now

        params = (
            task_id,
            task.get("status", "unknown"),
            task.get("context_id"),
            _dumps(task.get("message")),
            _dumps(task.get("history") or []),
            task.get("semantic_status"),
            task.get("completion_reason"),
            _dumps(task.get("artifact")),
            task.get("error"),
            created_at,
            now,
        )

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, status, context_id, message_json, history_json,
                    semantic_status, completion_reason, artifact_json,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    context_id = excluded.context_id,
                    message_json = excluded.message_json,
                    history_json = excluded.history_json,
                    semantic_status = excluded.semantic_status,
                    completion_reason = excluded.completion_reason,
                    artifact_json = excluded.artifact_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                params,
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Fetch one task by id. Returns ``None`` if not found."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            )
            row = cur.fetchone()
        return self._row_to_task(row) if row else None

    def list(
        self,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks, newest first. Optional ``status`` filter."""
        sql = "SELECT * FROM tasks"
        params: tuple[Any, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params = params + (int(limit),)

        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [self._row_to_task(r) for r in rows]

    def delete(self, task_id: str) -> bool:
        """Delete one task. Returns ``True`` if the row existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM tasks WHERE id = ?", (task_id,)
            )
            return cur.rowcount > 0

    def prune(self) -> int:
        """Remove tasks exceeding ``max_tasks`` or older than ``ttl_seconds``.

        Returns the total number of rows deleted.
        """
        deleted_total = 0
        ttl_cutoff_iso: str | None = None
        if self.ttl_seconds and self.ttl_seconds > 0:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - self.ttl_seconds
            ttl_cutoff_iso = datetime.fromtimestamp(
                cutoff_ts, tz=timezone.utc
            ).isoformat()

        with self._lock:
            # 1) Drop anything past TTL.
            if ttl_cutoff_iso is not None:
                cur = self._conn.execute(
                    "DELETE FROM tasks WHERE created_at < ?",
                    (ttl_cutoff_iso,),
                )
                deleted_total += cur.rowcount or 0

            # 2) Cap total rows to max_tasks (keep newest by created_at).
            if self.max_tasks and self.max_tasks > 0:
                cur = self._conn.execute(
                    """
                    DELETE FROM tasks
                    WHERE id IN (
                        SELECT id FROM tasks
                        ORDER BY created_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (int(self.max_tasks),),
                )
                deleted_total += cur.rowcount or 0

        return deleted_total

    def close(self) -> None:
        """Close the underlying SQLite connection. Idempotent."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.ProgrammingError:
                # Already closed — fine.
                pass

    # Context-manager sugar.
    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
