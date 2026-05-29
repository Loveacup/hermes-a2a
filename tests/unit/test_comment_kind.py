"""P0-3 unit tests: DCI bypass-table migration (方案 D).

Plan: s6m-config/docs/tdd-test-plan.md §3.3.1 (v1.1)
Migration: s6m-config/migrations/001_a2a_comment_kinds.sql

Validates that the bypass table approach delivers the DCI 14-kind contract
without touching upstream Hermes schema:
  - U1: table/view/version-table structure matches migration
  - U2: a2a_thread_view LEFT JOIN against seeded historical comments
  - U3: kind CHECK constraint enforces the 14 DCI values
  - U4: in_reply_to references resolve through the view
  - U5: a2a_schema_versions idempotency (INSERT OR IGNORE)
  - U6: metadata json_valid CHECK rejects invalid JSON

NB on U2 — the plan's "217 historical comments" figure refers to the
production database; isolated unit tests seed their own N rows and assert
on N (the semantics are identical: a thread_view row per task_comments row,
with has_a2a_record=0 when no bypass record exists yet).
"""
import sqlite3
import time

import pytest


DCI_KINDS = [
    "propose", "ask", "evidence_for", "evidence_against",
    "challenge", "clarify", "refine", "concede",
    "synthesize", "summarize", "meta_directive",
    "vote_for", "vote_against", "abstain",
]

EXPECTED_KIND_COLUMNS = {
    "comment_id":  "INTEGER",
    "task_id":     "TEXT",
    "kind":        "TEXT",
    "in_reply_to": "INTEGER",
    "metadata":    "TEXT",
    "created_at":  "INTEGER",
    "schema_ver":  "INTEGER",
}


def _conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _seed_comment(conn, task_id, body, author="default"):
    cur = conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (task_id, author, body),
    )
    conn.commit()
    return cur.lastrowid


def _seed_task(conn, tid="t-unit-1"):
    conn.execute(
        "INSERT OR IGNORE INTO tasks (id, title, status, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (tid, "seed", "ready"),
    )
    conn.commit()
    return tid


# ─── U1 ──────────────────────────────────────────────────────
def test_p03_unit_table_structure__matches_migration(a2a_migration_applied):
    """U1: a2a_comment_kinds / view / version-table created with correct shape."""
    conn = _conn(a2a_migration_applied)
    try:
        objs = {row["name"]: row["type"] for row in conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE name LIKE 'a2a_%'"
        )}
        assert objs.get("a2a_comment_kinds") == "table", (
            f"main table missing or wrong type; got: {objs}"
        )
        assert objs.get("a2a_thread_view") == "view"
        assert objs.get("a2a_schema_versions") == "table"

        cols = {r["name"]: r["type"].upper()
                for r in conn.execute("PRAGMA table_info(a2a_comment_kinds)")}
        missing = [c for c in EXPECTED_KIND_COLUMNS if c not in cols]
        assert not missing, f"a2a_comment_kinds missing columns: {missing}"
        for col, want in EXPECTED_KIND_COLUMNS.items():
            assert want in cols[col], (
                f"a2a_comment_kinds.{col} expected {want}, got {cols[col]}"
            )
    finally:
        conn.close()


# ─── U2 ──────────────────────────────────────────────────────
def test_p03_unit_thread_view__joins_all_comments(a2a_migration_applied):
    """U2: thread view returns one row per task_comments row, defaults kind='propose'."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-view-1")
        n = 5
        for i in range(n):
            _seed_comment(conn, tid, f"comment #{i}")

        rows = conn.execute(
            "SELECT comment_id, kind, has_a2a_record FROM a2a_thread_view "
            "WHERE task_id = ? ORDER BY comment_id", (tid,),
        ).fetchall()
        assert len(rows) == n, (
            f"view should return {n} rows for {n} comments, got {len(rows)}"
        )
        for r in rows:
            assert r["kind"] == "propose", (
                f"unmapped comments should default kind='propose', got {r['kind']!r}"
            )
            assert r["has_a2a_record"] == 0
    finally:
        conn.close()


# ─── U3 ──────────────────────────────────────────────────────
def test_p03_unit_kind_check__rejects_invalid(a2a_migration_applied):
    """U3: all 14 DCI kinds accepted; any other value triggers CHECK constraint."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-kinds-1")

        # Each of the 14 kinds writes successfully
        for kind in DCI_KINDS:
            cid = _seed_comment(conn, tid, f"body for {kind}")
            conn.execute(
                "INSERT INTO a2a_comment_kinds "
                "(comment_id, task_id, kind, created_at) "
                "VALUES (?, ?, ?, strftime('%s','now'))",
                (cid, tid, kind),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM a2a_comment_kinds WHERE task_id = ?",
            (tid,),
        ).fetchone()[0]
        assert count == len(DCI_KINDS), (
            f"want {len(DCI_KINDS)} kind rows, got {count}"
        )

        # Invalid kind must fail
        bad_cid = _seed_comment(conn, tid, "bad kind body")
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute(
                "INSERT INTO a2a_comment_kinds "
                "(comment_id, task_id, kind, created_at) "
                "VALUES (?, ?, ?, strftime('%s','now'))",
                (bad_cid, tid, "nonsense"),
            )
            conn.commit()
        assert "CHECK" in str(exc.value).upper() or "constraint" in str(exc.value).lower()
    finally:
        conn.close()


# ─── U4 ──────────────────────────────────────────────────────
def test_p03_unit_in_reply_to__valid_reference(a2a_migration_applied):
    """U4: in_reply_to references resolve through the view."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-reply-1")
        first_cid = _seed_comment(conn, tid, "I propose X")
        second_cid = _seed_comment(conn, tid, "I challenge X")

        conn.executemany(
            "INSERT INTO a2a_comment_kinds "
            "(comment_id, task_id, kind, in_reply_to, created_at) "
            "VALUES (?, ?, ?, ?, strftime('%s','now'))",
            [
                (first_cid,  tid, "propose",   None),
                (second_cid, tid, "challenge", first_cid),
            ],
        )
        conn.commit()

        rows = conn.execute(
            "SELECT comment_id, kind, in_reply_to FROM a2a_thread_view "
            "WHERE task_id = ? ORDER BY comment_id", (tid,),
        ).fetchall()
        assert rows[0]["kind"] == "propose"
        assert rows[0]["in_reply_to"] is None
        assert rows[1]["kind"] == "challenge"
        assert rows[1]["in_reply_to"] == first_cid

        # Self-reply must be rejected by CHECK
        cid3 = _seed_comment(conn, tid, "self-ref")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO a2a_comment_kinds "
                "(comment_id, task_id, kind, in_reply_to, created_at) "
                "VALUES (?, ?, ?, ?, strftime('%s','now'))",
                (cid3, tid, "refine", cid3),
            )
            conn.commit()
    finally:
        conn.close()


# ─── U5 ──────────────────────────────────────────────────────
def test_p03_unit_schema_versions__idempotent_migration(
    kanban_db, a2a_migration_applied,
):
    """U5: re-applying the migration leaves a2a_schema_versions stable."""
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent.parent
    sql = (ROOT / "s6m-config" / "migrations" /
           "001_a2a_comment_kinds.sql").read_text()

    conn = _conn(a2a_migration_applied)
    try:
        before = conn.execute(
            "SELECT version, note FROM a2a_schema_versions ORDER BY version"
        ).fetchall()
        assert len(before) == 1
        assert before[0]["version"] == 1

        # Apply again — must not raise, must not duplicate
        conn.executescript(sql)
        conn.commit()

        after = conn.execute(
            "SELECT version, note FROM a2a_schema_versions ORDER BY version"
        ).fetchall()
        assert len(after) == 1, (
            f"re-applied migration duplicated version row: {[dict(r) for r in after]}"
        )
        assert after[0]["version"] == 1
        assert after[0]["note"] == before[0]["note"]
    finally:
        conn.close()


# ─── U6 ──────────────────────────────────────────────────────
def test_p03_unit_metadata_json__rejects_invalid(a2a_migration_applied):
    """U6: metadata CHECK(json_valid) blocks malformed payloads."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-meta-1")

        # Valid JSON object: ok
        ok_cid = _seed_comment(conn, tid, "evidence body")
        conn.execute(
            "INSERT INTO a2a_comment_kinds "
            "(comment_id, task_id, kind, metadata, created_at) "
            "VALUES (?, ?, ?, ?, strftime('%s','now'))",
            (ok_cid, tid, "evidence_for",
             '{"source_url": "https://arxiv.org/abs/2603.11781"}'),
        )
        conn.commit()

        # Empty default '{}' set via DEFAULT clause: ok
        empty_cid = _seed_comment(conn, tid, "plain propose")
        conn.execute(
            "INSERT INTO a2a_comment_kinds "
            "(comment_id, task_id, kind, created_at) "
            "VALUES (?, ?, ?, strftime('%s','now'))",
            (empty_cid, tid, "propose"),
        )
        conn.commit()

        # Invalid JSON: must fail
        bad_cid = _seed_comment(conn, tid, "bad metadata")
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute(
                "INSERT INTO a2a_comment_kinds "
                "(comment_id, task_id, kind, metadata, created_at) "
                "VALUES (?, ?, ?, ?, strftime('%s','now'))",
                (bad_cid, tid, "challenge", "this is not json {"),
            )
            conn.commit()
        msg = str(exc.value).lower()
        assert "check" in msg or "constraint" in msg, (
            f"expected CHECK constraint violation; got: {exc.value}"
        )
    finally:
        conn.close()
