#!/usr/bin/env python3
"""E2E Phase 1B: DCI comment_kind routing + Scheme D bypass table (v2).

Tests:
  B1: Live-kanban comment routing — post a comment, classify, record,
      then assert orchestrator_router picks the expected next-actor profile.
  B2: Verify Scheme D bypass table (a2a_comment_kinds) is populated correctly
      after B1 writes.
  B3: Verify upstream task_comments isolation (no schema change).

Rewrite rationale (replaces v1):
  v1 created tasks via `kanban create --assignee shangshu` and embedded
  "kind=CHALLENGE" as a string in the task body, expecting the dispatcher to
  parse that keyword and reassign the task. That conflated two different
  routing layers:
    (A) Task assignment — hermes upstream dispatcher reads task.assignee
    (B) Comment routing — orchestrator_router.route_comment over thread entries
  Only layer (B) is implemented; v1 tested layer (A) with layer (B)'s
  expectations. v1 also referenced kinds that don't exist (VETO, DECISION)
  and wrong targets (EVIDENCE_FOR → budget; actual: archivist).

  v2 exercises the real layer (B) pipeline: create anchor task → post
  comment → classify → record_kind → route_comment → assert target.

Prerequisites:
  - Phase 1A completed
  - Scheme D migration applied (001_a2a_comment_kinds.sql) — production
  - 16/16 A2A healthy (not strictly needed; classifier + router are pure)

Usage:
  python tests/e2e/test_p0_b2_comment_kind.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "core"))

import comment_kind as ck                # noqa: E402
import comment_kind_classifier as cls    # noqa: E402
import orchestrator_router as orx        # noqa: E402

KANBAN_DB = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "kanban.db"

# (body_template, expected_kind, expected_target_profile)
# Body templates chosen to hit either the prefix or the heuristic path of
# comment_kind_classifier. Targets come from orchestrator_router.ROUTE_BY_KIND.
ROUTING_CASES = [
    ("[CHALLENGE] 该方案在并发写入下数据丢失",
     ck.CommentKind.CHALLENGE, "regent"),
    ("[ASK] 请问当前 retry 策略指数退避还是固定间隔",
     ck.CommentKind.ASK, "hanlinyuan"),
    ("根据 G²CP 论文 arxiv 2602.13370 的数据显示，结构化通信可减少 73% token",
     ck.CommentKind.EVIDENCE_FOR, "archivist"),
    ("[evidence_against] 实测数据反驳：延迟反而升高 12%",
     ck.CommentKind.EVIDENCE_AGAINST, "archivist"),
    ("【监国处置】此事须仲裁",
     ck.CommentKind.META_DIRECTIVE, "regent"),
    ("[SYNTHESIZE] 综合各方意见，先小流量再全量",
     ck.CommentKind.SYNTHESIZE, "regent"),
]


# ─── helpers ────────────────────────────────────────────────────

def hermes(*args, timeout=30):
    p = subprocess.run(["hermes", *args], capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def kanban_create_anchor(title: str) -> str | None:
    """Create a minimal anchor task; assignee doesn't matter for routing."""
    stdout, stderr, rc = hermes(
        "kanban", "create", title, "--assignee", "default",
        "--body", "B test anchor — not for spawn", "--json",
    )
    if rc != 0:
        print(f"  ❌ kanban create failed: {stderr}")
        return None
    try:
        return json.loads(stdout).get("id")
    except json.JSONDecodeError:
        return None


def kanban_post_comment(task_id: str, body: str) -> bool:
    """Post a comment via the CLI so it goes through the real write path."""
    stdout, stderr, rc = hermes("kanban", "comment", task_id, body)
    if rc != 0:
        print(f"  ❌ comment failed: {stderr}")
        return False
    return True


def latest_comment_id_for(conn, task_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM task_comments WHERE task_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return row[0] if row else None


def kanban_archive(task_id: str) -> None:
    hermes("kanban", "archive", task_id, timeout=15)


# ─── B1: live comment routing pipeline ──────────────────────────

def test_b1_comment_routing_pipeline():
    print("\n" + "=" * 60)
    print("B1: comment → classify → record_kind → route_comment (live kanban)")
    print("=" * 60)

    if not KANBAN_DB.exists():
        print(f"  ❌ kanban.db missing at {KANBAN_DB}")
        return False

    # Sanity: bypass table must exist (migration applied)
    conn = sqlite3.connect(str(KANBAN_DB))
    conn.row_factory = sqlite3.Row
    try:
        ok = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='a2a_comment_kinds'"
        ).fetchone()
        if not ok:
            print("  ❌ a2a_comment_kinds table missing — apply migration first")
            return False
        print("  ✅ Scheme D table present")

        results: dict[str, str] = {}
        created_tasks: list[str] = []

        for idx, (body, expected_kind, expected_target) in enumerate(ROUTING_CASES, 1):
            label = expected_kind.value
            print(f"\n  --- [{idx}/{len(ROUTING_CASES)}] {label} → {expected_target} ---")
            print(f"      body: {body[:60]}")

            tid = kanban_create_anchor(f"E2E-B1-v2 [{label}]")
            if not tid:
                results[label] = "CREATE_FAILED"
                continue
            created_tasks.append(tid)

            if not kanban_post_comment(tid, body):
                results[label] = "COMMENT_FAILED"
                continue

            # Give SQLite a beat to flush the comment write
            time.sleep(0.3)

            cid = latest_comment_id_for(conn, tid)
            if cid is None:
                results[label] = "NO_COMMENT_ROW"
                continue

            # Classify
            classified = cls.classify(body)
            if classified is None:
                results[label] = "CLASSIFY_NONE"
                print(f"      ❌ classifier returned None for body")
                continue
            if classified != expected_kind:
                results[label] = f"CLASSIFY_MISMATCH: got {classified.value}"
                print(f"      ❌ classifier returned {classified.value}, "
                      f"expected {expected_kind.value}")
                continue
            print(f"      ✓ classifier: {classified.value}")

            # Record into bypass table
            try:
                ck.record_kind(conn, comment_id=cid, kind=classified, task_id=tid)
            except (ValueError, sqlite3.IntegrityError) as e:
                results[label] = f"RECORD_FAILED: {e}"
                continue

            # Read thread + grab the entry we just recorded
            thread = ck.get_thread(conn, tid)
            entry = next((e for e in thread if e.comment_id == cid), None)
            if entry is None:
                results[label] = "THREAD_MISS"
                continue
            if entry.kind != expected_kind.value:
                results[label] = f"VIEW_KIND_MISMATCH: {entry.kind}"
                continue
            print(f"      ✓ a2a_thread_view: kind={entry.kind} "
                  f"has_a2a_record={entry.has_a2a_record}")

            # Route the comment via orchestrator
            routing = orx.route_comment(entry)
            if routing is None:
                results[label] = "NO_ROUTING"
                print(f"      ❌ route_comment returned None (kind has no route)")
                continue
            if routing.target_profile != expected_target:
                results[label] = (f"ROUTE_MISMATCH: target={routing.target_profile} "
                                  f"expected={expected_target}")
                print(f"      ❌ route → {routing.target_profile}, "
                      f"expected {expected_target}")
                continue

            results[label] = "PASS"
            print(f"      ✅ route → {routing.target_profile} "
                  f"({routing.reason})")

        # Cleanup
        for tid in created_tasks:
            kanban_archive(tid)

        pass_count = sum(1 for v in results.values() if v == "PASS")
        total = len(results)
        print(f"\n  B1 Summary: {pass_count}/{total} routing pipelines passed")
        if pass_count < total:
            print("  Failures:")
            for k, v in results.items():
                if v != "PASS":
                    print(f"    {k}: {v}")
        return pass_count == total
    finally:
        conn.close()


# ─── B2: bypass table population ─────────────────────────────────

def test_b2_bypass_table_population():
    print("\n" + "=" * 60)
    print("B2: Scheme D bypass table population (kinds from B1 writes)")
    print("=" * 60)
    if not KANBAN_DB.exists():
        print(f"  ❌ kanban.db missing at {KANBAN_DB}")
        return False

    conn = sqlite3.connect(str(KANBAN_DB))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM a2a_comment_kinds"
        ).fetchone()[0]
        print(f"  📊 Total rows in a2a_comment_kinds: {count}")

        kinds_present = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT kind FROM a2a_comment_kinds"
            )
        }
        print(f"  🏷️  Distinct kinds: {sorted(kinds_present)}")

        expected = {kind.value for _, kind, _ in ROUTING_CASES}
        missing = expected - kinds_present
        if missing:
            print(f"  ❌ Missing kinds (B1 should have populated): {sorted(missing)}")
            return False
        print(f"  ✅ All expected kinds present: {sorted(expected)}")
        return True
    finally:
        conn.close()


# ─── B3: upstream isolation ─────────────────────────────────────

def test_b3_upstream_isolation():
    print("\n" + "=" * 60)
    print("B3: Upstream task_comments isolation check")
    print("=" * 60)
    conn = sqlite3.connect(str(KANBAN_DB))
    try:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(task_comments)")
        ]
        forbidden = ["kind", "in_reply_to"]
        violations = [c for c in forbidden if c in columns]
        print(f"  📋 task_comments columns: {columns}")
        if violations:
            print(f"  ❌ FAIL: upstream table modified! Found: {violations}")
            return False
        print(f"  ✅ PASS: upstream task_comments untouched (no kind/in_reply_to)")
        return True
    finally:
        conn.close()


def main():
    results = {
        "B1": test_b1_comment_routing_pipeline(),
        "B2": test_b2_bypass_table_population(),
        "B3": test_b3_upstream_isolation(),
    }

    print("\n" + "=" * 60)
    print("PHASE 1B RESULTS (v2 — comment-routing pipeline)")
    print("=" * 60)
    for name, passed in results.items():
        print(f"  {name}: {'✅ PASS' if passed else '❌ FAIL'}")
    all_pass = all(results.values())
    print(f"\n  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
