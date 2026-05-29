"""P0-1 integration tests: card lifecycle, daemon, dispatch decisions.

Plan: s6m-config/docs/tdd-test-plan.md §1.2.2 (v1.1)

Design notes (empirically validated against hermes v0.15.1):
- `hermes kanban create` defaults to status='ready' (not 'triage' as in §7.2).
  Lifecycle test asserts reachable transitions + task_events chain rather
  than a strict 5-state walk.
- `task_events.kind` is the column name (NOT event_type).
- `task_runs.status` writes 'completed' on done (not 'done' as schema comment hints).
- `dispatch --dry-run --json` returns `spawned: [{task_id, assignee, workspace}]`
  for ready tasks — this is the unspawned-decision signal used by I4.
- Daemon fixture intentionally uses `hermes kanban daemon`, not
  `hermes gateway start`, to avoid pulling up the messaging gateway.
"""
import json
import os
import signal
import sqlite3
import subprocess
import time

import pytest


# ─── helpers ──────────────────────────────────────────────────

def _hermes(tmp_home, *args, check=True, json_out=False, timeout=60):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_home)
    env["HOME"] = str(tmp_home.parent)
    result = subprocess.run(
        ["hermes", *args],
        env=env, text=True, capture_output=True,
        check=check, timeout=timeout,
    )
    if json_out:
        return json.loads(result.stdout)
    return result


def _create_card(tmp_home, title, assignee="default", **kw):
    extra = []
    for k, v in kw.items():
        extra += [f"--{k.replace('_','-')}", str(v)]
    out = _hermes(tmp_home, "kanban", "create", title,
                  "--assignee", assignee, "--json", *extra, json_out=True)
    return out["id"]


def _conn(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _events(conn, task_id):
    rows = conn.execute(
        "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return [r["kind"] for r in rows]


def _status(conn, task_id):
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return row["status"] if row else None


# ─── I1 ───────────────────────────────────────────────────────
def test_p01_integ_lifecycle__triage_to_done(tmp_hermes_home, kanban_db):
    """I1: card walks reachable lifecycle and task_events records each step.

    v1.1 design: defaults to ready (not triage); we walk
    ready → blocked → ready → done and assert the event chain
    contains the four expected transitions.
    """
    tid = _create_card(tmp_hermes_home, "lifecycle probe")
    conn = _conn(kanban_db)

    assert _status(conn, tid) == "ready"
    assert "created" in _events(conn, tid)

    _hermes(tmp_hermes_home, "kanban", "block", tid, "need input")
    assert _status(conn, tid) == "blocked"

    _hermes(tmp_hermes_home, "kanban", "unblock", tid)
    assert _status(conn, tid) == "ready", (
        f"unblock should return to ready, got {_status(conn, tid)}"
    )

    _hermes(tmp_hermes_home, "kanban", "complete", tid,
            "--summary", "done", "--metadata", "{}")
    assert _status(conn, tid) == "done"

    chain = _events(conn, tid)
    must_include = {"created", "blocked", "completed"}
    missing = must_include - set(chain)
    assert not missing, (
        f"task_events chain missing {missing}; got {chain}"
    )
    conn.close()


# ─── I2 ───────────────────────────────────────────────────────
def test_p01_integ_daemon__starts_and_pidfile(dispatcher_daemon):
    """I2: hermes kanban daemon writes pidfile and process is alive."""
    pidfile = dispatcher_daemon["pidfile"]
    proc = dispatcher_daemon["proc"]

    # Polled in fixture up to 5s; verify post-condition
    assert pidfile.exists(), (
        f"daemon pidfile not written at {pidfile} within 5s; "
        f"proc.poll={proc.poll()}"
    )
    written_pid = int(pidfile.read_text().strip())
    assert written_pid == proc.pid, (
        f"pidfile pid {written_pid} != proc.pid {proc.pid}"
    )
    # Process must still be running
    assert proc.poll() is None, (
        f"daemon exited prematurely: rc={proc.returncode}"
    )


# ─── I3 ───────────────────────────────────────────────────────
def test_p01_integ_daemon__sigterm_clean_shutdown(dispatcher_daemon):
    """I3: SIGTERM causes clean shutdown within 5s and pidfile is removed."""
    proc = dispatcher_daemon["proc"]
    pidfile = dispatcher_daemon["pidfile"]

    # Make sure it's actually running before we signal
    assert proc.poll() is None, "daemon not running at sigterm probe start"
    assert pidfile.exists(), "pidfile missing before sigterm"

    proc.send_signal(signal.SIGTERM)
    try:
        rc = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("daemon did not exit within 5s of SIGTERM")
    assert rc == 0, f"daemon exited non-zero on SIGTERM: rc={rc}"
    assert not pidfile.exists(), (
        f"pidfile not cleaned up post-sigterm: {pidfile}"
    )


# ─── I4 ───────────────────────────────────────────────────────
def test_p01_integ_dispatcher__claims_ready_task(
    tmp_hermes_home, kanban_db, dry_run_dispatcher,
):
    """I4: dispatcher sees a ready task and decides to spawn it (dry-run).

    We use --dry-run instead of really spawning to avoid LLM calls in CI.
    """
    tid = _create_card(tmp_hermes_home, "claim me", assignee="regent")

    decision = dry_run_dispatcher()
    spawned_ids = {s["task_id"] for s in decision["spawned"]}
    assert tid in spawned_ids, (
        f"dispatcher did not pick {tid} (assignee=regent); "
        f"decision={decision}"
    )
    spawn = next(s for s in decision["spawned"] if s["task_id"] == tid)
    assert spawn["assignee"] == "regent"


# ─── I5 ───────────────────────────────────────────────────────
def test_p01_integ_complete__writes_summary_metadata(
    tmp_hermes_home, kanban_db,
):
    """I5: kanban complete writes summary + metadata into task_runs."""
    tid = _create_card(tmp_hermes_home, "deliverable")
    metadata = {"changed_files": ["a.py", "b.py"], "tests_run": 7}
    _hermes(tmp_hermes_home, "kanban", "complete", tid,
            "--summary", "shipped the deliverable",
            "--metadata", json.dumps(metadata))

    conn = _conn(kanban_db)
    row = conn.execute(
        "SELECT status, outcome, summary, metadata FROM task_runs "
        "WHERE task_id = ?", (tid,),
    ).fetchone()
    conn.close()
    assert row is not None, "no task_runs row created on complete"
    assert row["outcome"] == "completed", (
        f"expected outcome='completed', got {row['outcome']!r}"
    )
    assert row["summary"] == "shipped the deliverable"
    assert json.loads(row["metadata"]) == metadata


# ─── I6 ───────────────────────────────────────────────────────
def test_p01_integ_block__pauses_for_human(
    tmp_hermes_home, kanban_db, dry_run_dispatcher,
):
    """I6: blocked task is not considered for spawn by dispatcher."""
    tid = _create_card(tmp_hermes_home, "needs human", assignee="default")

    # Sanity: ready task is spawnable
    pre = dry_run_dispatcher()
    assert tid in {s["task_id"] for s in pre["spawned"]}, (
        f"sanity failed: ready task not in spawn set; pre={pre}"
    )

    _hermes(tmp_hermes_home, "kanban", "block", tid, "ask the human first")

    conn = _conn(kanban_db)
    assert _status(conn, tid) == "blocked"
    conn.close()

    post = dry_run_dispatcher()
    spawned_ids = {s["task_id"] for s in post["spawned"]}
    assert tid not in spawned_ids, (
        f"blocked task should not be spawned; post={post}"
    )
