"""P0-1 unit tests: hermes kanban init in isolated HERMES_HOME.

Plan: s6m-config/docs/tdd-test-plan.md §1.2.1 (v1.1)
- U1: test_p01_unit_init_db__creates_six_tables
- U2: test_p01_unit_init_db__wal_mode_enabled
- U3: test_p01_unit_init_db__idempotent  (replaces v1.0 --force test)
- U4: test_p01_unit_tasks_schema__has_required_columns

RED stage: tests are written against fixtures only; no production code yet.
Tests should pass once the fixtures + hermes CLI deliver expected behavior.
The first run validates whether the fixture chain itself works end-to-end.
"""
import os
import sqlite3
import subprocess

import pytest


EXPECTED_TABLES = {
    "tasks",
    "task_links",
    "task_comments",
    "task_events",
    "task_runs",
    "kanban_notify_subs",
}

REQUIRED_TASK_COLUMNS = {
    "skills": "TEXT",
    "model_override": "TEXT",
    "current_run_id": "INTEGER",
    "claim_lock": "TEXT",
    "tenant": "TEXT",
}


def _list_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _columns_of(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: r[2].upper() for r in rows}


# ─── U1 ──────────────────────────────────────────────────────
def test_p01_unit_init_db__creates_six_tables(kanban_conn):
    """U1: kanban init creates the 6 core tables."""
    tables = _list_tables(kanban_conn)
    missing = EXPECTED_TABLES - tables
    assert not missing, (
        f"missing tables after kanban init: {sorted(missing)}; "
        f"got tables: {sorted(tables)}"
    )


# ─── U2 ──────────────────────────────────────────────────────
def test_p01_unit_init_db__wal_mode_enabled(kanban_conn):
    """U2: journal_mode is WAL (required for concurrent multi-profile writes)."""
    mode = kanban_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"want WAL, got {mode!r}"


# ─── U3 ──────────────────────────────────────────────────────
def test_p01_unit_init_db__idempotent(tmp_hermes_home, kanban_db, kanban_conn):
    """U3: running `hermes kanban init` twice does not error or wipe data.

    Plan v1.1: replaces v1.0 --force test (CLI has no --force flag).
    """
    # Seed a row to detect data loss across second init
    kanban_conn.execute(
        "INSERT INTO tasks (id, title, status, assignee, created_at) "
        "VALUES (?, ?, ?, ?, strftime('%s','now'))",
        ("t-idem-1", "idempotency probe", "todo", "default"),
    )
    kanban_conn.commit()

    # Re-run init; must not raise
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_hermes_home)
    env["HOME"] = str(tmp_hermes_home.parent)
    result = subprocess.run(
        ["hermes", "kanban", "init"],
        env=env, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"second init failed: rc={result.returncode}\n"
        f"stderr={result.stderr}\nstdout={result.stdout}"
    )

    # Verify the seeded row survives
    surviving = kanban_conn.execute(
        "SELECT title FROM tasks WHERE id = ?", ("t-idem-1",)
    ).fetchone()
    assert surviving is not None, "second kanban init wiped tasks"
    assert surviving[0] == "idempotency probe"


# ─── U4 ──────────────────────────────────────────────────────
def test_p01_unit_tasks_schema__has_required_columns(kanban_conn):
    """U4: tasks table has skills/model_override/current_run_id/claim_lock/tenant."""
    cols = _columns_of(kanban_conn, "tasks")
    missing = [c for c in REQUIRED_TASK_COLUMNS if c not in cols]
    assert not missing, (
        f"tasks table missing required columns: {missing}; "
        f"present: {sorted(cols)}"
    )
    for col, want_type in REQUIRED_TASK_COLUMNS.items():
        got = cols[col]
        assert want_type in got, (
            f"tasks.{col} expected type containing {want_type!r}, got {got!r}"
        )
