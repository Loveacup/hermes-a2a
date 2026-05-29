"""DCI typed-epistemic-act API for a2a_comment_kinds bypass table (方案 D).

Application-layer contract:
  - Stores DCI kind + in_reply_to + metadata next to each task_comments row
  - Validates the soft foreign key into task_comments at write time
  - Reads the joined a2a_thread_view for orchestrator consumption

This module does NOT modify upstream Hermes schema. The migration that creates
the bypass table is at s6m-config/migrations/001_a2a_comment_kinds.sql.

Plan: s6m-config/docs/tdd-test-plan.md §3.5 (v1.1)
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class CommentKind(str, Enum):
    """14 DCI typed epistemic acts (arXiv 2603.11781)."""
    PROPOSE          = "propose"
    ASK              = "ask"
    EVIDENCE_FOR     = "evidence_for"
    EVIDENCE_AGAINST = "evidence_against"
    CHALLENGE        = "challenge"
    CLARIFY          = "clarify"
    REFINE           = "refine"
    CONCEDE          = "concede"
    SYNTHESIZE       = "synthesize"
    SUMMARIZE        = "summarize"
    META_DIRECTIVE   = "meta_directive"
    VOTE_FOR         = "vote_for"
    VOTE_AGAINST     = "vote_against"
    ABSTAIN          = "abstain"


@dataclass(frozen=True)
class ThreadEntry:
    comment_id: int
    task_id: str
    author: str
    body: str
    kind: str
    in_reply_to: int | None
    metadata: dict
    created_at: int
    has_a2a_record: bool


def _coerce_kind(kind) -> str:
    """Accept either CommentKind enum or its string value."""
    if isinstance(kind, CommentKind):
        return kind.value
    if isinstance(kind, str):
        # Will be checked by sqlite CHECK constraint downstream too
        return kind
    raise TypeError(f"kind must be CommentKind or str, got {type(kind).__name__}")


def validate_soft_fk(conn: sqlite3.Connection, comment_id: int) -> None:
    """Raise ValueError if comment_id does not exist in task_comments.

    The bypass-table schema (方案 D) declares no SQL FK to task_comments
    because Hermes owns that table; we validate in application code.
    """
    row = conn.execute(
        "SELECT 1 FROM task_comments WHERE id = ?", (comment_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"comment_id {comment_id} not found in task_comments (soft FK)"
        )


def record_kind(
    conn: sqlite3.Connection,
    comment_id: int,
    kind,
    *,
    in_reply_to: int | None = None,
    metadata: dict | None = None,
    task_id: str | None = None,
    created_at: int | None = None,
) -> None:
    """Insert (or upsert) a kind record for an existing task_comments row.

    `task_id` is derivable from task_comments(comment_id) but accepted as an
    argument to avoid an extra read when the caller already knows it.
    """
    validate_soft_fk(conn, comment_id)
    if task_id is None:
        row = conn.execute(
            "SELECT task_id FROM task_comments WHERE id = ?", (comment_id,),
        ).fetchone()
        task_id = row[0]

    metadata_json = json.dumps(metadata) if metadata is not None else "{}"
    created = created_at if created_at is not None else int(time.time())

    conn.execute(
        "INSERT OR REPLACE INTO a2a_comment_kinds "
        "(comment_id, task_id, kind, in_reply_to, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (comment_id, task_id, _coerce_kind(kind),
         in_reply_to, metadata_json, created),
    )
    conn.commit()


def get_thread(conn: sqlite3.Connection, task_id: str) -> list[ThreadEntry]:
    """Read the joined a2a_thread_view for a task, time-ordered."""
    rows = conn.execute(
        "SELECT comment_id, task_id, author, body, kind, "
        "in_reply_to, metadata, created_at, has_a2a_record "
        "FROM a2a_thread_view WHERE task_id = ? "
        "ORDER BY created_at, comment_id",
        (task_id,),
    ).fetchall()
    out: list[ThreadEntry] = []
    for r in rows:
        try:
            meta = json.loads(r[6]) if r[6] else {}
        except json.JSONDecodeError:
            meta = {}
        out.append(ThreadEntry(
            comment_id=r[0], task_id=r[1], author=r[2], body=r[3],
            kind=r[4], in_reply_to=r[5], metadata=meta,
            created_at=r[7], has_a2a_record=bool(r[8]),
        ))
    return out


def count_by_kind(thread: Iterable[ThreadEntry]) -> dict[str, int]:
    """Counter helper used by orchestrator routing decisions."""
    tally: dict[str, int] = {}
    for e in thread:
        tally[e.kind] = tally.get(e.kind, 0) + 1
    return tally
