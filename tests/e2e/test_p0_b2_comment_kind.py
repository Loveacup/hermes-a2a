#!/usr/bin/env python3
"""E2E Phase 1B: DCI comment_kind routing + Scheme D bypass table.

Tests:
  B1: Create cards with different DCI kinds, verify correct routing
  B2: Verify Scheme D bypass table (a2a_comment_kinds) is populated correctly
  B3: Verify upstream task_comments isolation (no schema change)

The 14 DCI kinds from comment_kind.py:
  PROPOSE, EVIDENCE_FOR, EVIDENCE_AGAINST, CHALLENGE, CLARIFY,
  SYNTHESIZE, CONCEDE, ASK, VETO, DECISION, AMEND, REFER, NOTE, ACKNOWLEDGE

Key routing rules (from orchestrator_router.py ROUTE_BY_KIND):
  CHALLENGE → regent
  VETO → regent
  ASK → hanlinyuan
  EVIDENCE_FOR → budget
  EVIDENCE_AGAINST → budget
  DECISION → reviewer

Prerequisites:
  - Phase 1A completed
  - Scheme D migration applied (001_a2a_comment_kinds.sql)
  - 16/16 A2A healthy

Usage:
  python tests/e2e/test_p0_b2_comment_kind.py
"""

import os
import subprocess
import sys
import time
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
TIMEOUT_PER_CARD = 180
POLL_INTERVAL = 10
KANBAN_DB = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "kanban.db"

# Critical routing pairs: (kind, expected_assignee)
ROUTING_TESTS = [
    ("CHALLENGE", "regent", "挑战类必须路由到太子"),
    ("VETO", "regent", "否决类必须路由到太子"),
    ("ASK", "hanlinyuan", "提问类路由到翰林院"),
    ("EVIDENCE_FOR", "budget", "举证支持路由到户部"),
    ("DECISION", "reviewer", "决策类路由到门下省"),
]


def hermes(*args, timeout=30):
    cmd = ["hermes"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def kanban_create(title, assignee, skill, body, timeout=30):
    """Create card and return ID."""
    args = ["kanban", "create", title, "--assignee", assignee, "--body", body, "--json"]
    if skill:
        args.extend(["--skill", skill])
    stdout, stderr, rc = hermes(*args, timeout=timeout)
    if rc != 0:
        print(f"  ❌ create failed: {stderr}")
        return None
    try:
        data = json.loads(stdout)
        return data.get("id")
    except json.JSONDecodeError:
        for line in stdout.split("\n"):
            for p in line.split():
                if p.startswith("t_"):
                    return p
    return None


def kanban_show(card_id, timeout=30):
    stdout, stderr, rc = hermes("kanban", "show", card_id, "--json", timeout=timeout)
    if rc != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None

def card_status(card):
    if card is None:
        return "unknown"
    return card.get("task", {}).get("status", "unknown")

def card_assignee(card):
    if card is None:
        return "unknown"
    return card.get("task", {}).get("assignee", "unknown")

def card_summary(card):
    if card is None:
        return ""
    return card.get("latest_summary", "") or ""


def wait_for_completion(card_id, timeout=TIMEOUT_PER_CARD):
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        card = kanban_show(card_id)
        if card is None:
            time.sleep(POLL_INTERVAL)
            continue
        status = card_status(card)
        if status != last_status:
            print(f"  [{int(time.time()-start)}s] {card_id}: {status}")
            last_status = status
        if status in ("done", "blocked", "cancelled"):
            return card
        time.sleep(POLL_INTERVAL)
    print(f"  ⏰ TIMEOUT — last: {last_status}")
    return kanban_show(card_id)


def check_scheme_d_table():
    """Verify a2a_comment_kinds table exists and has correct schema."""
    db_path = str(KANBAN_DB)
    if not Path(db_path).exists():
        # Try alternate path
        db_path = "/Users/alexcai/.hermes/kanban.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='a2a_comment_kinds'"
        )
        exists = cursor.fetchone() is not None
        if exists:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(a2a_comment_kinds)")]
            conn.close()
            return True, columns
        conn.close()
        return False, []
    except Exception as e:
        return False, str(e)


def test_b1_routing_table():
    """B1: Verify DCI kind routing works for known pairs.

    For each (kind, expected_assignee), create a card and verify it
    was dispatched to the correct profile.
    """
    print("\n" + "="*60)
    print("B1: DCI kind routing verification")
    print("="*60)

    # First check Scheme D table
    table_ok, columns = check_scheme_d_table()
    if table_ok:
        print(f"  ✅ Scheme D table exists: columns={columns}")
    else:
        print(f"  ⚠️  Scheme D table check: {columns}")
        # Don't fail — table may exist but at different path

    results = {}
    for kind, expected_assignee, reason in ROUTING_TESTS:
        print(f"\n  --- {kind} → {expected_assignee} ({reason}) ---")

        card_id = kanban_create(
            title=f"E2E-B1: {kind} routing test",
            assignee="shangshu",  # Start at dispatcher, let it route
            skill=None,
            body=f"这是一个 DCI kind={kind} 的测试消息。请确认你收到的任务，并说明你是哪个部门的 agent。"
        )

        if not card_id:
            results[kind] = "CREATE_FAILED"
            continue

        print(f"  📋 Card: {card_id}")
        card = wait_for_completion(card_id)

        if not card:
            results[kind] = "TIMEOUT"
            continue

        actual_assignee = card_assignee(card)
        status = card_status(card)

        # Check: was it assigned to expected profile?
        match = actual_assignee == expected_assignee
        results[kind] = "PASS" if match else f"MISMATCH: got {actual_assignee}"

        status_icon = "✅" if match else "❌"
        print(f"  {status_icon} {kind}: expected={expected_assignee}, actual={actual_assignee}, status={status}")

    return results


def test_b2_bypass_table_population():
    """B2: Verify Scheme D table is populated with correct data.

    After B1 creates cards with DCI kinds, check the a2a_comment_kinds
    table for corresponding entries.
    """
    print("\n" + "="*60)
    print("B2: Scheme D bypass table population")
    print("="*60)

    db_path = "/Users/alexcai/.hermes/kanban.db"
    if not Path(db_path).exists():
        print(f"  ⚠️  kanban.db not at {db_path}")
        return False

    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM a2a_comment_kinds").fetchone()[0]
        print(f"  📊 Total rows in a2a_comment_kinds: {count}")

        # Check for rows with our test kinds
        kinds_found = set()
        for row in conn.execute(
            "SELECT DISTINCT kind FROM a2a_comment_kinds ORDER BY kind"
        ):
            kinds_found.add(row[0])

        print(f"  🏷️  Distinct kinds in table: {sorted(kinds_found)}")

        # Verify at least some of our test kinds are present
        test_kinds = {k for k, _, _ in ROUTING_TESTS}
        present = kinds_found & test_kinds

        if present:
            print(f"  ✅ Test kinds found: {sorted(present)}")
        else:
            print(f"  ⚠️  No test kinds found in table (may need B1 to run first)")

        conn.close()
        return len(present) > 0

    except sqlite3.OperationalError as e:
        print(f"  ❌ SQLite error: {e}")
        return False


def test_b3_upstream_isolation():
    """B3: Verify upstream task_comments table is NOT modified.

    The Scheme D approach guarantees zero changes to Hermes upstream.
    Verify task_comments still has no 'kind' or 'in_reply_to' columns.
    """
    print("\n" + "="*60)
    print("B3: Upstream task_comments isolation check")
    print("="*60)

    db_path = "/Users/alexcai/.hermes/kanban.db"
    try:
        conn = sqlite3.connect(db_path)
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(task_comments)")
        ]
        conn.close()

        forbidden = ["kind", "in_reply_to"]
        violations = [c for c in forbidden if c in columns]

        print(f"  📋 task_comments columns: {columns}")
        if violations:
            print(f"  ❌ FAIL: upstream table modified! Found: {violations}")
            return False
        else:
            print(f"  ✅ PASS: upstream task_comments untouched (no kind/in_reply_to)")
            return True

    except Exception as e:
        print(f"  ⚠️  Could not verify: {e}")
        return False


def main():
    results = {}

    # B1: routing test
    routing_results = test_b1_routing_table()
    pass_count = sum(1 for v in routing_results.values() if v == "PASS")
    fail_count = len(routing_results) - pass_count
    print(f"\n  B1 Summary: {pass_count}/{len(routing_results)} routing tests passed")
    results["B1"] = pass_count == len(routing_results)

    # B2: table population
    results["B2"] = test_b2_bypass_table_population()

    # B3: upstream isolation
    results["B3"] = test_b3_upstream_isolation()

    print("\n" + "="*60)
    print("PHASE 1B RESULTS")
    print("="*60)
    for name, passed in results.items():
        print(f"  {name}: {'✅ PASS' if passed else '❌ FAIL'}")

    all_pass = all(results.values())
    print(f"\n  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
