"""P1-A unit tests for _ensure_comment_kind_backfill hook in task_handler.

Contract:
  - Task-scoped backfill classifies every unclassified comment for the task.
  - Global sweep is bounded by limit=100 to keep per-tick cost predictable.
  - Idempotent on the bypass row (INSERT OR REPLACE).
  - Missing kanban.db or missing bypass table → silent no-op (no raise).
  - handle_task() invokes the hook after task execution.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "core"))

MIGRATION_SQL = ROOT / "s6m-config" / "migrations" / "001_a2a_comment_kinds.sql"


def _make_kanban_with_bypass(home: Path) -> Path:
    db = home / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.executescript(MIGRATION_SQL.read_text())
    conn.commit()
    conn.close()
    return db


def _seed(db: Path, rows: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(str(db))
    base = 1_700_000_000
    for i, (task_id, author, body) in enumerate(rows):
        conn.execute(
            "INSERT INTO task_comments(task_id, author, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, author, body, base + i),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _make_kanban_with_bypass(home)
    return home


def test_hook_classifies_task_scope(kanban_home):
    db = kanban_home / "kanban.db"
    _seed(db, [
        ("t_a", "regent", "[CHALLENGE] 这个方案有问题"),
        ("t_a", "engineer", "建议改成异步队列"),
        ("t_b", "default", "其他任务的评论"),
    ])

    from task_handler import _ensure_comment_kind_backfill
    result = _ensure_comment_kind_backfill(task_id="t_a")
    assert result is not None
    assert result["classified"] + result["defaulted"] >= 2

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT comment_id, kind FROM a2a_comment_kinds "
        "WHERE task_id='t_a' ORDER BY comment_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0][1] == "challenge"
    assert rows[1][1] == "refine"  # 建议改成 → REFINE


def test_hook_idempotent(kanban_home):
    db = kanban_home / "kanban.db"
    _seed(db, [("t_x", "regent", "[PROPOSE] 提议方案 A")])

    from task_handler import _ensure_comment_kind_backfill
    _ensure_comment_kind_backfill(task_id="t_x")
    _ensure_comment_kind_backfill(task_id="t_x")

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM a2a_comment_kinds WHERE task_id='t_x'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_hook_sweep_limit_100(kanban_home):
    db = kanban_home / "kanban.db"
    _seed(db, [("t_sweep", "engineer", f"评论 {i}") for i in range(150)])

    from task_handler import _ensure_comment_kind_backfill
    result = _ensure_comment_kind_backfill()  # no task_id → global sweep
    assert result is not None

    conn = sqlite3.connect(str(db))
    total = conn.execute("SELECT COUNT(*) FROM a2a_comment_kinds").fetchone()[0]
    conn.close()
    assert total == 100, f"sweep should be bounded to 100, got {total}"


def test_hook_no_db_silent_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty"))
    from task_handler import _ensure_comment_kind_backfill
    assert _ensure_comment_kind_backfill(task_id="anything") is None


def test_hook_no_bypass_table_silent_noop(tmp_path, monkeypatch):
    home = tmp_path / "raw"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = home / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id TEXT, author TEXT, body TEXT, created_at INTEGER);"
        "INSERT INTO task_comments(task_id, author, body, created_at) "
        "VALUES ('t', 'a', '提议方案', 1700000000);"
    )
    conn.commit()
    conn.close()

    from task_handler import _ensure_comment_kind_backfill
    assert _ensure_comment_kind_backfill(task_id="t") is None


def test_handle_task_invokes_hook(kanban_home, monkeypatch):
    db = kanban_home / "kanban.db"
    _seed(db, [("t_h", "engineer", "[PROPOSE] 通过钩子写入")])

    import task_handler
    # Short-circuit the heavy execution paths so handle_task only exercises
    # the dispatch wrapper that should invoke the backfill hook on exit.
    def fake_api(task, tid, prompt, profile):
        task["status"] = "completed"
        task["artifact"] = {"response": "ok"}
        return task

    def fake_sub(task, tid, prompt, profile):
        return fake_api(task, tid, prompt, profile)

    monkeypatch.setattr(task_handler, "_via_api_server", fake_api)
    monkeypatch.setattr(task_handler, "_via_subprocess", fake_sub)
    monkeypatch.setenv("HERMES_PROFILE", "default")

    result = task_handler.handle_task({"id": "t_h", "message": "test"})
    assert result["status"] == "completed"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT kind FROM a2a_comment_kinds WHERE task_id='t_h'"
    ).fetchone()
    conn.close()
    assert row is not None, "hook did not run after handle_task"
    assert row[0] == "propose"
