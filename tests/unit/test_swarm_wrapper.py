"""W4 unit tests: kanban swarm wrapper (DCI gate decision).

Plan: orchestrator_router 拓扑层迁移 — kanban swarm wrapper (保留 vote/deadlock)。

What this module covers:
  - W4-U1  dry_run 返回拓扑骨架，不触 DB
  - W4-U2  create_swarm 真正写入：root/workers/verifier/synthesizer 四档齐备
  - W4-U3  decide_gate 空线程 → block:empty_thread
  - W4-U4  decide_gate 多数 VOTE_FOR → gate=pass
  - W4-U5  decide_gate 多数 VOTE_AGAINST → block，route_hint 走聚合器
  - W4-U6  decide_gate 末条 SYNTHESIZE / CONCEDE → gate=pass:converging
  - W4-U7  decide_gate 连续 3 条 CHALLENGE → 死锁 → route_hint=regent
  - W4-U8  decide_gate 末条 ASK → block，route_hint=hanlinyuan

约束自检：测试仅消费 core/orchestrator_router.py + core/comment_kind.py 的现成
导出，验证 wrapper 没有偏离 W4 任务约束（不改这两个文件）。
"""
from __future__ import annotations

import sqlite3
import time

import pytest


# ─────────────────────────────────────────────────────────────────────────
#  共享 helpers — 直接写底层表，跳过 hermes 调用，保持单测纯净
# ─────────────────────────────────────────────────────────────────────────

def _conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _seed_task(conn, tid="t-w4-root"):
    conn.execute(
        "INSERT OR IGNORE INTO tasks (id, title, status, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (tid, "swarm root", "ready"),
    )
    conn.commit()
    return tid


def _seed_comment(conn, task_id, body, *, author="default"):
    cur = conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (task_id, author, body),
    )
    conn.commit()
    return cur.lastrowid


def _attach_kind(conn, comment_id, task_id, kind, *, in_reply_to=None,
                 created_at=None):
    ts = created_at if created_at is not None else int(time.time())
    conn.execute(
        "INSERT INTO a2a_comment_kinds "
        "(comment_id, task_id, kind, in_reply_to, metadata, created_at) "
        "VALUES (?, ?, ?, ?, '{}', ?)",
        (comment_id, task_id, kind, in_reply_to, ts),
    )
    conn.commit()


def _post(conn, task_id, kind, *, author="worker", body=None,
          in_reply_to=None, created_at=None):
    body = body or f"<{kind}>"
    cid = _seed_comment(conn, task_id, body, author=author)
    _attach_kind(conn, cid, task_id, kind, in_reply_to=in_reply_to,
                 created_at=created_at)
    return cid


# ─────────────────────────────────────────────────────────────────────────
#  W4-U1  dry_run 不触 DB
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u1_create_swarm_dry_run_returns_topology(a2a_migration_applied):
    """dry_run=True 返回拓扑骨架 + verifier_body_suffix，不写 kanban.db。"""
    from swarm_wrapper import WorkerSpec, create_swarm

    conn = _conn(a2a_migration_applied)
    try:
        before_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        result = create_swarm(
            conn,
            goal="W4 dry-run validation",
            workers=[
                WorkerSpec(profile="hanlinyuan", title="research"),
                WorkerSpec(profile="archivist", title="archive"),
            ],
            verifier="regent",
            synthesizer="default",
            dry_run=True,
        )
        after_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert after_tasks == before_tasks, (
            "dry_run 不应修改 tasks 表 — but got delta "
            f"{after_tasks - before_tasks}"
        )
        assert result["dry_run"] is True
        assert result["goal"] == "W4 dry-run validation"
        assert result["verifier"] == "regent"
        assert result["synthesizer"] == "default"
        assert len(result["workers"]) == 2
        assert "DCI gate protocol" in result["verifier_body_suffix"]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U2  真实写入：root/workers/verifier/synthesizer 拓扑齐备
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u2_create_swarm_writes_full_topology(a2a_migration_applied):
    """create_swarm 实际写库时，四档任务齐备且 id 不为空。"""
    from swarm_wrapper import WorkerSpec, create_swarm

    conn = _conn(a2a_migration_applied)
    try:
        created = create_swarm(
            conn,
            goal="W4 real swarm graph",
            workers=[
                WorkerSpec(profile="hanlinyuan", title="research"),
                WorkerSpec(profile="archivist", title="archive"),
            ],
            verifier="regent",
            synthesizer="default",
            created_by="regent",
        )
        assert created["root_id"], "missing root_id"
        assert len(created["worker_ids"]) == 2
        assert created["verifier_id"]
        assert created["synthesizer_id"]
        assert "DCI gate protocol" in created["verifier_body_suffix"]
        # workers ≠ verifier ≠ synthesizer
        ids = {created["root_id"], created["verifier_id"],
               created["synthesizer_id"], *created["worker_ids"]}
        assert len(ids) == 5
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U3  空线程 → block:empty_thread
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u3_decide_gate_empty_thread(a2a_migration_applied):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-empty")
        v = decide_gate(conn, tid)
        assert v.gate == "block"
        assert v.reason == "empty_thread"
        assert v.deadlocked is False
        assert v.tally.total == 0
        assert v.route_hint is None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U4  多数 VOTE_FOR → gate=pass
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u4_decide_gate_majority_for_passes(a2a_migration_applied):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-vote-for")
        _post(conn, tid, "vote_for", author="hanlinyuan", created_at=1)
        _post(conn, tid, "vote_for", author="archivist", created_at=2)
        _post(conn, tid, "vote_against", author="default", created_at=3)
        v = decide_gate(conn, tid)
        assert v.gate == "pass"
        assert "majority_for" in v.reason
        assert v.tally.for_ == 2
        assert v.tally.against == 1
        assert v.deadlocked is False
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U5  多数 VOTE_AGAINST → block；route_hint 走聚合器
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u5_decide_gate_majority_against_blocks(a2a_migration_applied):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-vote-against")
        _post(conn, tid, "vote_against", author="hanlinyuan", created_at=1)
        _post(conn, tid, "vote_against", author="archivist", created_at=2)
        _post(conn, tid, "vote_for", author="default", created_at=3)
        v = decide_gate(conn, tid)
        assert v.gate == "block"
        assert v.tally.against == 2
        assert v.tally.for_ == 1
        # 末条 vote_for 走聚合器
        assert v.route_hint is not None
        assert v.route_hint.is_aggregator is True
        assert v.route_hint.target_profile is None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U6  末条 SYNTHESIZE / CONCEDE → gate=pass:converging
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["synthesize", "concede"])
def test_w4_u6_decide_gate_converging_last_entry(
    a2a_migration_applied, kind,
):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, f"t-converging-{kind}")
        _post(conn, tid, "challenge", author="hanlinyuan", created_at=1)
        _post(conn, tid, "evidence_for", author="archivist", created_at=2)
        _post(conn, tid, kind, author="regent", created_at=3)
        v = decide_gate(conn, tid)
        assert v.gate == "pass"
        assert v.reason.startswith("converging:") and kind in v.reason
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U7  连续 3 条 CHALLENGE → 死锁 → route_hint=regent
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u7_decide_gate_deadlock_routes_to_regent(a2a_migration_applied):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-deadlock")
        _post(conn, tid, "challenge", author="hanlinyuan", created_at=1)
        _post(conn, tid, "challenge", author="archivist", created_at=2)
        _post(conn, tid, "challenge", author="default", created_at=3)
        v = decide_gate(conn, tid)
        assert v.gate == "block"
        assert v.deadlocked is True
        assert v.reason.startswith("deadlock")
        assert v.route_hint is not None
        assert v.route_hint.target_profile == "regent"
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
#  W4-U8  末条 ASK → block，route_hint=hanlinyuan
# ─────────────────────────────────────────────────────────────────────────

def test_w4_u8_decide_gate_ask_routes_to_hanlinyuan(a2a_migration_applied):
    from swarm_wrapper import decide_gate

    conn = _conn(a2a_migration_applied)
    try:
        tid = _seed_task(conn, "t-ask")
        _post(conn, tid, "propose", author="hanlinyuan", created_at=1)
        _post(conn, tid, "ask", author="regent", created_at=2)
        v = decide_gate(conn, tid)
        assert v.gate == "block"
        assert v.route_hint is not None
        assert v.route_hint.target_profile == "hanlinyuan"
        assert "kind:ask" in v.route_hint.reason
    finally:
        conn.close()
