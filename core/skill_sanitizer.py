"""Drop unknown skills from ``tasks.skills`` so the dispatcher never hands an
unresolvable ``--skills`` arg to the worker.

Why this exists
---------------
Upstream hermes' kanban dispatcher passes ``task.skills`` straight through to
``hermes ... chat --skills X``. The worker rejects unknown skills with
``ValueError: Unknown skill(s): X`` (cli.py:15054), which the dispatcher logs
as a crash and reschedules → tight crash loop on a typo.

hermes-a2a's :mod:`skill_resolver` already tolerates unknowns (warn + drop).
This module brings the same policy to the kanban path by mutating the
``tasks.skills`` column in-place. Behaviour:

* ``sanitize(conn, task_id)`` — for one task, resolve each requested skill
  via :func:`skill_resolver.locate_skill`; rewrite the column to keep only
  the resolvable names. Logs the dropped names. No-op when nothing changes.
* ``sweep_ready(conn)`` — apply ``sanitize`` to every row in status
  ``ready`` / ``todo``. Called by the watcher loop and by tests.
* CLI: ``python -m skill_sanitizer --once`` for one-shot sweep,
  ``--watch --interval 2`` for the daemon mode.

Race window
-----------
The dispatcher tick (≥5s) may claim a freshly-created task before the
sanitizer sweeps it. We mitigate by sweeping ``ready`` / ``todo`` *and*
``running`` / ``blocked``: the worker reads ``tasks.skills`` at spawn time,
so if a crashing task slipped past as ``ready``, the next promote → claim
cycle still picks up the cleaned column. With a 2s interval this catches
the crash-loop within a few ticks.

Not a replacement for input validation — operators should still avoid
typos at create time. This is the degraded-but-functional fallback.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_resolver import locate_skill  # noqa: E402

logger = logging.getLogger("hermes-a2a.skill_sanitizer")

DEFAULT_DB = Path(os.environ.get("HERMES_HOME",
                                 Path.home() / ".hermes")) / "kanban.db"
SWEEP_STATUSES = ("ready", "todo", "running", "blocked")


def _load_skills(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []


def sanitize(conn: sqlite3.Connection, task_id: str) -> tuple[list[str], list[str]]:
    """Drop unresolvable skills from one task. Returns (kept, dropped)."""
    row = conn.execute(
        "SELECT assignee, skills FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    if row is None:
        return [], []
    profile = row[0] or "default"
    requested = _load_skills(row[1])
    if not requested:
        return [], []

    kept: list[str] = []
    dropped: list[str] = []
    for name in requested:
        if locate_skill(name, profile) is not None:
            kept.append(name)
        else:
            dropped.append(name)

    if not dropped:
        return kept, []

    new_raw = json.dumps(kept)
    conn.execute(
        "UPDATE tasks SET skills = ? WHERE id = ?",
        (new_raw, task_id),
    )
    conn.commit()
    logger.warning(
        "skill_sanitizer: task=%s profile=%s dropped=%s kept=%s",
        task_id, profile, dropped, kept,
    )
    return kept, dropped


def sweep_ready(conn: sqlite3.Connection) -> dict[str, int]:
    """Sanitize every ready/todo task. Returns counters."""
    rows = conn.execute(
        "SELECT id FROM tasks "
        f"WHERE status IN ({','.join('?' * len(SWEEP_STATUSES))}) "
        "AND skills IS NOT NULL AND skills != '[]'",
        SWEEP_STATUSES,
    ).fetchall()
    touched = 0
    dropped_total = 0
    for (tid,) in rows:
        _, dropped = sanitize(conn, tid)
        if dropped:
            touched += 1
            dropped_total += len(dropped)
    return {"scanned": len(rows), "touched": touched, "dropped": dropped_total}


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help="path to kanban.db (default: $HERMES_HOME/kanban.db)")
    p.add_argument("--once", action="store_true",
                   help="run a single sweep and exit")
    p.add_argument("--watch", action="store_true",
                   help="run sweep on a loop (default mode if neither set)")
    p.add_argument("--interval", type=float, default=2.0,
                   help="seconds between sweeps in --watch mode (default 2.0)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: kanban.db not found at {db_path}", file=sys.stderr)
        return 2

    if args.once or not args.watch:
        with _open(db_path) as conn:
            r = sweep_ready(conn)
        logger.info("sweep complete: %s", r)
        return 0

    logger.info("watcher starting db=%s interval=%.1fs", db_path, args.interval)
    while True:
        try:
            with _open(db_path) as conn:
                r = sweep_ready(conn)
            if r["touched"]:
                logger.info("swept: %s", r)
        except sqlite3.DatabaseError as e:
            logger.warning("db error (will retry): %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
