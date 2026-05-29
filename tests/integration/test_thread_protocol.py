"""P0-3 integration tests: DCI bypass-table API + orchestrator routing.

Plan: s6m-config/docs/tdd-test-plan.md §3.3.2 (v1.1)

- I1: record_kind + get_thread roundtrip (PROPOSE + CHALLENGE → joined view)
- I2: validate_soft_fk rejects nonexistent task_comments.id
- I3: in_reply_to chain preserves the conversation tree
- I4: route_comment(CHALLENGE) → regent  (太子仲裁 path)
- I5: aggregate_votes counts 2 FOR / 1 AGAINST / 1 ABSTAIN → 2:1:1, majority='for'
- I6: detect_deadlock fires after 3 same-kind, non-converging comments
- I7: migration is idempotent on a production-clone db (217 historical comments)
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

# core/ is on sys.path via conftest.py
import comment_kind as ck  # noqa: E402
import orchestrator_router as orx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION_SQL = ROOT / "s6m-config" / "migrations" / "001_a2a_comment_kinds.sql"


def _conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _seed_task(conn, tid):
    conn.execute(
        "INSERT OR IGNORE INTO tasks (id, title, status, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (tid, "seed", "ready"),
    )
    conn.commit()


def _seed_comment(conn, tid, body, author="default"):
    cur = conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (tid, author, body),
    )
    conn.commit()
    return cur.lastrowid


# ─── I1 ───────────────────────────────────────────────────────
def test_p03_integ_record_and_get_thread__roundtrip(a2a_migration_applied):
    """I1: record_kind → get_thread roundtrip preserves kind + metadata."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = "t-i1-roundtrip"
        _seed_task(conn, tid)
        c1 = _seed_comment(conn, tid, "I propose strategy A", author="default")
        c2 = _seed_comment(conn, tid, "I challenge: assumes B", author="regent")

        ck.record_kind(conn, c1, ck.CommentKind.PROPOSE)
        ck.record_kind(conn, c2, ck.CommentKind.CHALLENGE,
                       in_reply_to=c1,
                       metadata={"hidden_assumption": "B"})

        thread = ck.get_thread(conn, tid)
        assert len(thread) == 2
        assert thread[0].kind == "propose"
        assert thread[0].author == "default"
        assert thread[0].has_a2a_record is True
        assert thread[1].kind == "challenge"
        assert thread[1].in_reply_to == c1
        assert thread[1].metadata == {"hidden_assumption": "B"}
    finally:
        conn.close()


# ─── I2 ───────────────────────────────────────────────────────
def test_p03_integ_soft_fk__rejects_unknown_comment_id(a2a_migration_applied):
    """I2: validate_soft_fk raises ValueError when comment_id is orphan."""
    conn = _conn(a2a_migration_applied)
    try:
        with pytest.raises(ValueError, match="not found in task_comments"):
            ck.record_kind(
                conn, comment_id=999_999_999,
                kind=ck.CommentKind.PROPOSE,
                task_id="t-orphan",
            )
        # The bypass table must remain empty after the failed write
        n = conn.execute(
            "SELECT COUNT(*) FROM a2a_comment_kinds"
        ).fetchone()[0]
        assert n == 0, f"orphan write leaked into table: {n} rows"
    finally:
        conn.close()


# ─── I3 ───────────────────────────────────────────────────────
def test_p03_integ_in_reply_to__three_step_chain(a2a_migration_applied):
    """I3: 3-comment reply chain (C1 → C2 → C3) reads back intact."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = "t-i3-chain"
        _seed_task(conn, tid)
        c1 = _seed_comment(conn, tid, "P", "hanlinyuan")
        c2 = _seed_comment(conn, tid, "Q", "gongbu")
        c3 = _seed_comment(conn, tid, "R", "regent")

        ck.record_kind(conn, c1, ck.CommentKind.PROPOSE)
        ck.record_kind(conn, c2, ck.CommentKind.CHALLENGE, in_reply_to=c1)
        ck.record_kind(conn, c3, ck.CommentKind.SYNTHESIZE, in_reply_to=c2)

        thread = ck.get_thread(conn, tid)
        assert [e.in_reply_to for e in thread] == [None, c1, c2]
        assert [e.kind for e in thread] == ["propose", "challenge", "synthesize"]

        # Walk the chain from the leaf
        by_id = {e.comment_id: e for e in thread}
        leaf = by_id[c3]
        walk = []
        while leaf is not None:
            walk.append(leaf.comment_id)
            leaf = by_id.get(leaf.in_reply_to) if leaf.in_reply_to else None
        assert walk == [c3, c2, c1]
    finally:
        conn.close()


# ─── I4 ───────────────────────────────────────────────────────
def test_p03_integ_orchestrator__challenge_routes_to_regent(a2a_migration_applied):
    """I4: CHALLENGE comment → orchestrator routes to regent (太子仲裁)."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = "t-i4-route"
        _seed_task(conn, tid)
        c1 = _seed_comment(conn, tid, "P", "gongbu")
        c2 = _seed_comment(conn, tid, "C", "tester")
        ck.record_kind(conn, c1, ck.CommentKind.PROPOSE)
        ck.record_kind(conn, c2, ck.CommentKind.CHALLENGE, in_reply_to=c1)

        thread = ck.get_thread(conn, tid)
        # PROPOSE has no routing target (orchestrator waits)
        assert orx.route_comment(thread[0]) is None
        # CHALLENGE routes to regent
        r = orx.route_comment(thread[1])
        assert r is not None
        assert r.target_profile == "regent"
        assert r.reason == "kind:challenge"
        assert r.is_aggregator is False

        # Confirm ASK routes to hanlinyuan as well (路由表健全检查)
        c3 = _seed_comment(conn, tid, "?", "shangshu")
        ck.record_kind(conn, c3, ck.CommentKind.ASK)
        ask_entry = ck.get_thread(conn, tid)[-1]
        ask_route = orx.route_comment(ask_entry)
        assert ask_route is not None and ask_route.target_profile == "hanlinyuan"
    finally:
        conn.close()


# ─── I5 ───────────────────────────────────────────────────────
def test_p03_integ_vote_aggregation__counts_correctly(a2a_migration_applied):
    """I5: 2 FOR / 1 AGAINST / 1 ABSTAIN → tally 2:1:1, majority='for'."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = "t-i5-vote"
        _seed_task(conn, tid)

        votes = [
            (ck.CommentKind.VOTE_FOR,     "default"),
            (ck.CommentKind.VOTE_FOR,     "regent"),
            (ck.CommentKind.VOTE_AGAINST, "tester"),
            (ck.CommentKind.ABSTAIN,      "archivist"),
        ]
        for kind, author in votes:
            cid = _seed_comment(conn, tid, f"vote by {author}", author=author)
            ck.record_kind(conn, cid, kind)

        thread = ck.get_thread(conn, tid)
        tally = orx.aggregate_votes(thread)
        assert (tally.for_, tally.against, tally.abstain) == (2, 1, 1)
        assert tally.total == 4
        assert tally.majority == "for"

        # Every vote routes to the internal aggregator
        for e in thread:
            r = orx.route_comment(e)
            assert r is not None and r.is_aggregator
            assert r.target_profile is None
    finally:
        conn.close()


# ─── I6 ───────────────────────────────────────────────────────
def test_p03_integ_deadlock_guard__triggers_after_three_repeats(a2a_migration_applied):
    """I6: 3 consecutive CHALLENGE → deadlock; CONCEDE on top → cleared."""
    conn = _conn(a2a_migration_applied)
    try:
        tid = "t-i6-deadlock"
        _seed_task(conn, tid)

        # First a PROPOSE so the thread is not vacuous
        c0 = _seed_comment(conn, tid, "open", "default")
        ck.record_kind(conn, c0, ck.CommentKind.PROPOSE)
        thread = ck.get_thread(conn, tid)
        assert orx.detect_deadlock(thread) is False

        # 3 consecutive CHALLENGE — deadlock condition
        for i in range(3):
            cid = _seed_comment(conn, tid, f"again #{i}", "auditor")
            ck.record_kind(conn, cid, ck.CommentKind.CHALLENGE)
        thread = ck.get_thread(conn, tid)
        assert orx.detect_deadlock(thread) is True

        # The auto-response routes to regent
        resp = orx.deadlock_response(thread)
        assert resp is not None
        assert resp.target_profile == "regent"
        assert "deadlock" in resp.reason

        # A converging CONCEDE on top breaks the deadlock
        cid_concede = _seed_comment(conn, tid, "ok let's drop it", "tester")
        ck.record_kind(conn, cid_concede, ck.CommentKind.CONCEDE)
        thread = ck.get_thread(conn, tid)
        assert orx.detect_deadlock(thread) is False
        assert orx.deadlock_response(thread) is None
    finally:
        conn.close()


# ─── I7 ───────────────────────────────────────────────────────
def test_p03_integ_migration__idempotent_on_production_clone(tmp_path):
    """I7: applying the migration twice on a copy of the production db is safe.

    Production db contains 217 historical comments; the joined view should
    expose all of them with kind='propose' default and has_a2a_record=0.
    Re-applying the migration must not duplicate the version row or alter
    historical comment counts.
    """
    src = Path.home() / ".hermes" / "kanban.db"
    if not src.exists() or src.stat().st_size == 0:
        pytest.skip("production kanban.db missing or empty")

    dst = tmp_path / "kanban_clone.db"
    shutil.copy(src, dst)

    sql = MIGRATION_SQL.read_text()
    conn = sqlite3.connect(str(dst))
    try:
        # Apply
        conn.executescript(sql)
        conn.commit()
        n_before = conn.execute(
            "SELECT COUNT(*) FROM task_comments"
        ).fetchone()[0]
        v_before = conn.execute(
            "SELECT COUNT(*) FROM a2a_schema_versions"
        ).fetchone()[0]
        view_before = conn.execute(
            "SELECT COUNT(*) FROM a2a_thread_view"
        ).fetchone()[0]
        assert n_before > 0, "production clone has no comments"
        assert v_before == 1
        assert view_before == n_before, (
            f"view count {view_before} != task_comments count {n_before}"
        )

        # Re-apply
        conn.executescript(sql)
        conn.commit()
        v_after = conn.execute(
            "SELECT COUNT(*) FROM a2a_schema_versions"
        ).fetchone()[0]
        view_after = conn.execute(
            "SELECT COUNT(*) FROM a2a_thread_view"
        ).fetchone()[0]
        assert v_after == 1, (
            f"re-applied migration duplicated schema version row: {v_after}"
        )
        assert view_after == n_before
        # Existing comments still default to kind='propose' (no bypass record)
        default_kind_count = conn.execute(
            "SELECT COUNT(*) FROM a2a_thread_view "
            "WHERE has_a2a_record = 0 AND kind = 'propose'"
        ).fetchone()[0]
        assert default_kind_count == n_before
    finally:
        conn.close()
