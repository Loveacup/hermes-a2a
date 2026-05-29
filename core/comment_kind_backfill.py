"""Backfill a2a_comment_kinds for task_comments that have no bypass record.

Two entry points:
  - backfill_task(conn, task_id) — classify+record every unclassified
    comment for a single task. Used by orchestrator after a discussion turn.
  - backfill_all(conn, limit=None) — sweep mode for catch-up after a
    deployment that introduced the bypass table mid-flight.

The classifier defaults are conservative: bodies that don't match any
pattern get kind=PROPOSE (the legacy default already exposed by
a2a_thread_view's COALESCE), so backfill always converges.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from comment_kind import CommentKind, record_kind
from comment_kind_classifier import classify

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillResult:
    classified: int        # explicit prefix or heuristic match
    defaulted: int         # nothing matched → PROPOSE
    skipped: int           # already had an a2a_comment_kinds row
    by_kind: dict[str, int]


def _unclassified_comments(conn: sqlite3.Connection, task_id: str | None):
    """Yield (id, task_id, body) for comments without bypass records."""
    if task_id is None:
        q = (
            "SELECT c.id, c.task_id, c.body "
            "FROM task_comments c "
            "LEFT JOIN a2a_comment_kinds k ON k.comment_id = c.id "
            "WHERE k.comment_id IS NULL "
            "ORDER BY c.created_at, c.id"
        )
        params: tuple = ()
    else:
        q = (
            "SELECT c.id, c.task_id, c.body "
            "FROM task_comments c "
            "LEFT JOIN a2a_comment_kinds k ON k.comment_id = c.id "
            "WHERE k.comment_id IS NULL AND c.task_id = ? "
            "ORDER BY c.created_at, c.id"
        )
        params = (task_id,)
    yield from conn.execute(q, params)


def backfill(
    conn: sqlite3.Connection,
    *,
    task_id: str | None = None,
    limit: int | None = None,
    default_kind: CommentKind = CommentKind.PROPOSE,
) -> BackfillResult:
    """Classify and record every unclassified comment.

    Returns a BackfillResult summarising classification outcomes.
    """
    classified = 0
    defaulted = 0
    skipped = 0
    by_kind: dict[str, int] = {}

    for cid, tid, body in _unclassified_comments(conn, task_id):
        if limit is not None and (classified + defaulted) >= limit:
            break
        guess = classify(body or "")
        if guess is not None:
            kind = guess
            classified += 1
        else:
            kind = default_kind
            defaulted += 1
        try:
            record_kind(conn, comment_id=cid, kind=kind, task_id=tid)
            by_kind[kind.value] = by_kind.get(kind.value, 0) + 1
        except (ValueError, sqlite3.IntegrityError) as e:
            logger.warning("backfill: skipping comment %s on %s: %s", cid, tid, e)
            skipped += 1

    return BackfillResult(
        classified=classified,
        defaulted=defaulted,
        skipped=skipped,
        by_kind=by_kind,
    )


def backfill_task(conn: sqlite3.Connection, task_id: str) -> BackfillResult:
    """Convenience: backfill a single task."""
    return backfill(conn, task_id=task_id)


def backfill_all(conn: sqlite3.Connection,
                 limit: int | None = None) -> BackfillResult:
    """Sweep-mode backfill across every task."""
    return backfill(conn, task_id=None, limit=limit)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import os

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-id", help="restrict to a single task")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--db",
                   default=os.path.expanduser("~/.hermes/kanban.db"))
    args = p.parse_args()

    db = sqlite3.connect(args.db)
    try:
        result = backfill(db, task_id=args.task_id, limit=args.limit)
        print(f"classified : {result.classified}")
        print(f"defaulted  : {result.defaulted}")
        print(f"skipped    : {result.skipped}")
        print("by_kind    :")
        for k, n in sorted(result.by_kind.items()):
            print(f"  {k:18s} {n}")
    finally:
        db.close()
