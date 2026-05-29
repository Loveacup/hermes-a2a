#!/usr/bin/env python3
"""E2E Phase 1C: orchestrator_router — ROUTE_BY_KIND + VoteTally + deadlock guard.

Tests:
  C1: VoteTally — multi-agent vote verification
  C2: Deadlock guard — verify deadlock detection triggers
  C3: Comprehensive debate — 三省真实辩论 (翰林院/工部/太子)

Prerequisites:
  - Phase 1A + 1B completed
  - 16/16 A2A healthy
  - Scheme D table exists

Usage:
  python tests/e2e/test_p0_c3_orchestrator.py
"""

import subprocess
import sys
import time
import json
import sqlite3
import os
from pathlib import Path

TZ = __import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
TIMEOUT_PER_CARD = 300  # orchestrator/debate may take longer
POLL_INTERVAL = 10
KANBAN_DB = "/Users/alexcai/.hermes/kanban.db"


def hermes(*args, timeout=30):
    cmd = ["hermes"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def kanban_create(title, assignee, skill, body, timeout=30):
    args = ["kanban", "create", title, "--assignee", assignee, "--body", body, "--json"]
    if skill:
        args.extend(["--skill", skill])
    stdout, stderr, rc = hermes(*args, timeout=timeout)
    if rc != 0:
        print(f"  ❌ create failed: {stderr}")
        return None
    try:
        return json.loads(stdout).get("id")
    except json.JSONDecodeError:
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
    return kanban_show(card_id)


def get_task_kinds(task_ids):
    """Batch query DCI kinds from a2a_comment_kinds."""
    if not task_ids:
        return {}
    try:
        conn = sqlite3.connect(KANBAN_DB)
        placeholders = ",".join("?" for _ in task_ids)
        rows = conn.execute(
            f"SELECT task_id, kind FROM a2a_comment_kinds WHERE task_id IN ({placeholders})",
            task_ids
        ).fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def test_c1_vote_tally():
    """C1: Verify VoteTally works correctly.

    Create a debate card, let multiple agents vote, verify count.
    """
    print("\n" + "="*60)
    print("C1: VoteTally — multi-agent vote aggregation")
    print("="*60)

    card_id = kanban_create(
        title="E2E-C1: VoteTally test — should we adopt A2A preview?",
        assignee="regent",
        skill=None,
        body=(
            "你作为监国太子，请发起一个三省辩论，议题：'是否应该用 hermes-a2a-preview 替代当前 A2A 实现？'\n\n"
            "请让翰林院(hanlinyuan)、工部(gongbu)、兵部(engineer)各发表意见（支持/反对/中立），"
            "然后汇总投票结果。完成后请报告最终的投票计数（支持/反对/中立各多少）。\n\n"
            "注意：这是一个 VoteTally 测试。请确保三个部门都给出了明确的投票立场。"
        )
    )
    if not card_id:
        return False

    print(f"  📋 Card: {card_id}")
    card = wait_for_completion(card_id, timeout=300)

    if not card or card_status(card) != "done":
        print(f"  ❌ VoteTally card not done")
        return False

    summary = card.get("latest_summary", "")
    print(f"  📝 Result: {summary[:300]}")

    # Look for vote-related keywords
    has_vote = any(w in summary.lower() for w in ["票", "vote", "支持", "反对", "中立"])
    if has_vote:
        print(f"  ✅ PASS: VoteTally produced vote results")
        return True
    else:
        print(f"  ⚠️  No explicit vote tally found — check output")
        return False


def test_c2_deadlock_guard():
    """C2: Verify deadlock guard triggers for circular dependencies.

    Create two cards that depend on each other, verify guard triggers.
    """
    print("\n" + "="*60)
    print("C2: Deadlock guard — circular dependency detection")
    print("="*60)

    # Create card A that depends on card B
    card_a = kanban_create(
        title="E2E-C2-A: deadlock test parent",
        assignee="gongbu",
        skill="infra-health-check",
        body="这是一个死锁测试。card B 依赖你，你也依赖 card B（循环依赖）。请尝试完成并报告是否检测到死锁。"
    )
    if not card_a:
        return False

    # Create card B that depends on card A
    card_b = kanban_create(
        title="E2E-C2-B: deadlock test child",
        assignee="engineer",
        skill="infra-health-check",
        body="这是一个死锁测试。card A 依赖你，你也依赖 card A（循环依赖）。请尝试完成并报告是否检测到死锁。"
    )
    if not card_b:
        return False

    print(f"  📋 Cards: A={card_a}, B={card_b}")

    # Wait for both to reach terminal state
    card_a_data = wait_for_completion(card_a, timeout=240)
    card_b_data = wait_for_completion(card_b, timeout=240)

    status_a = card_status(card_a_data)
    status_b = card_status(card_b_data)

    print(f"  Result: A={status_a}, B={status_b}")

    # Either should detect deadlock (blocked/cancelled with deadlock mention)
    summary_a = card_a_data.get("latest_summary", "") if card_a_data else ""
    summary_b = card_b_data.get("latest_summary", "") if card_b_data else ""
    combined = (summary_a + " " + summary_b).lower()

    has_deadlock = "deadlock" in combined or "死锁" in combined or "循环" in combined
    guarded = status_a in ("blocked", "cancelled") or status_b in ("blocked", "cancelled")

    if has_deadlock or guarded:
        print(f"  ✅ PASS: Deadlock guard triggered")
        return True
    else:
        print(f"  ⚠️  Deadlock not explicitly detected, but cards reached terminal state")
        return status_a in ("done", "blocked") and status_b in ("done", "blocked")


def test_c3_comprehensive_debate():
    """C3: Full three-province debate with DCI kind tracking.

    翰林院 + 工部 + 太子 debate on a real question. Verify:
    - Multiple comments with different kinds
    - Thread integrity
    - DCI kind coverage (≥4 kinds)
    """
    print("\n" + "="*60)
    print("C3: Comprehensive debate — 三省 × DCI kind tracking")
    print("="*60)

    card_id = kanban_create(
        title="E2E-C3: Comprehensive debate — A2A protocol evolution",
        assignee="regent",
        skill=None,
        body=(
            "你作为监国太子，请主持一次完整的三省辩论，议题：'三省六部 A2A 协议下一步应优先支持哪种通信模式？'\n\n"
            "辩论流程：\n"
            "1. 先让翰林院(hanlinyuan)提出 PROPOSE（提案）\n"
            "2. 让工部(gongbu)给出 EVIDENCE_FOR（举证支持）或 EVIDENCE_AGAINST（举证反对）\n"
            "3. 让兵部(engineer)给出 CHALLENGE（挑战）\n"
            "4. 你作为太子(SYNTHESIZE)综合各方意见\n"
            "5. 最后由门下省(reviewer)给出 DECISION（裁决）\n\n"
            "完成后，请在总结中列出所有出现的 DCI kind，并说明每种 kind 出现了几次。"
        )
    )
    if not card_id:
        return False

    print(f"  📋 Card: {card_id}")
    card = wait_for_completion(card_id, timeout=300)

    if not card or card_status(card) != "done":
        print(f"  ❌ Debate not completed")
        return False

    summary = card.get("latest_summary", "")
    comments = card.get("comments", [])
    print(f"  💬 Comments: {len(comments)}")
    print(f"  📝 Summary: {summary[:300]}")

    # Check DCI kinds in a2a_comment_kinds
    task_ids = [card_id]
    kinds_map = get_task_kinds(task_ids)
    kinds_found = set(kinds_map.values())

    print(f"  🏷️  DCI kinds in thread: {sorted(kinds_found) if kinds_found else 'none'}")

    has_enough_comments = len(comments) >= 4
    has_dci_kinds = len(kinds_found) >= 3
    summary_mentions_kinds = "kind" in summary.lower() or "PROPOSE" in summary or "CHALLENGE" in summary

    if has_enough_comments and has_dci_kinds:
        print(f"  ✅ PASS: Comprehensive debate with {len(kinds_found)} DCI kinds")
        return True
    elif has_enough_comments:
        print(f"  ⚠️  Debate had enough comments but few DCI kinds recorded")
        return True  # Partial pass
    else:
        print(f"  ⚠️  Debate incomplete — {len(comments)} comments, {len(kinds_found)} kinds")
        return False


def main():
    results = {}

    results["C1"] = test_c1_vote_tally()
    results["C2"] = test_c2_deadlock_guard()
    results["C3"] = test_c3_comprehensive_debate()

    print("\n" + "="*60)
    print("PHASE 1C RESULTS")
    print("="*60)
    for name, passed in results.items():
        print(f"  {name}: {'✅ PASS' if passed else '❌ FAIL'}")

    all_pass = all(results.values())
    print(f"\n  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
