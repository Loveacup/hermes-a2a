#!/usr/bin/env python3
"""E2E Phase 1A: skill_resolver path resolution & M2CL cross-dept loading.

Tests:
  A1: Verify gongbu worker loads its own dept skill (infra-health-check)
  A2: Verify engineer worker cross-loads gongbu's skill (M2CL)
  A3: Verify fallback chain for non-existent skill (graceful degradation)

Prerequisites:
  - 16/16 A2A servers healthy (hermes-a2a-doctor.sh)
  - Dispatcher daemon running (kanban dispatcher)
  - jz-skills at /Users/alexcai/code/jz-skills/

Usage:
  python tests/e2e/test_p0_a1_skill_resolver.py
  python tests/e2e/test_p0_a1_skill_resolver.py --verbose
"""

import subprocess
import sys
import time
import json
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
TIMEOUT_PER_CARD = 180  # max seconds to wait for a card to complete
POLL_INTERVAL = 10

def hermes(*args, timeout=30):
    """Run hermes CLI and return stdout."""
    cmd = ["hermes"] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip(), p.stderr.strip(), p.returncode

def kanban_create(title, assignee, skill, body, timeout=30):
    """Create a kanban card and return the card ID."""
    args = ["kanban", "create", title, "--assignee", assignee, "--body", body, "--json"]
    if skill:
        args.extend(["--skill", skill])
    stdout, stderr, rc = hermes(*args, timeout=timeout)
    if rc != 0:
        print(f"  ❌ kanban create failed: {stderr}")
        return None
    try:
        data = json.loads(stdout)
        return data.get("id")
    except json.JSONDecodeError:
        # Fallback: try to parse from text output
        for line in stdout.split("\n"):
            for p in line.split():
                if p.startswith("t_"):
                    return p
    return None

def kanban_show(card_id, timeout=30):
    """Get kanban card details as dict."""
    stdout, stderr, rc = hermes("kanban", "show", card_id, "--json", timeout=timeout)
    if rc != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None

def card_status(card):
    """Extract status from kanban show JSON."""
    if card is None:
        return "unknown"
    return card.get("task", {}).get("status", "unknown")

def card_assignee(card):
    """Extract assignee from kanban show JSON."""
    if card is None:
        return "unknown"
    return card.get("task", {}).get("assignee", "unknown")

def card_summary(card):
    """Extract latest_summary from kanban show JSON."""
    if card is None:
        return ""
    return card.get("latest_summary", "") or ""

def card_comments(card):
    """Extract comments from kanban show JSON."""
    if card is None:
        return []
    return card.get("comments", [])

def card_skills(card):
    """Extract skills list from kanban show JSON."""
    if card is None:
        return []
    return card.get("task", {}).get("skills", [])

def wait_for_completion(card_id, timeout=TIMEOUT_PER_CARD):
    """Poll until card reaches done/blocked/failed, or timeout."""
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        card = kanban_show(card_id)
        if card is None:
            time.sleep(POLL_INTERVAL)
            continue
        status = card_status(card)
        if status != last_status:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] {card_id}: {status}")
            last_status = status
        if status in ("done", "blocked", "cancelled"):
            return card
        time.sleep(POLL_INTERVAL)
    print(f"  ⏰ TIMEOUT after {timeout}s — last status: {last_status}")
    return kanban_show(card_id)

def verify_skill_loaded(card, skill_name):
    """Check if the card's summary/comments mention the skill was loaded."""
    summary = card_summary(card)
    comments = card_comments(card)

    evidence = summary
    for c in comments:
        evidence += " " + c.get("body", "")

    evidence_lower = evidence.lower()
    skill_lower = skill_name.lower().replace("-", "")

    # Heuristic: look for skill name mention or "loaded skills" list
    has_skill = skill_lower in evidence_lower.replace("-", "")
    has_loaded = "loaded" in evidence_lower or "skill" in evidence_lower

    return has_skill, evidence[:500]


def test_a1_gongbu_self_dept():
    """A1: gongbu worker loads its own dept skill (infra-health-check).

    gongbu has hermes-3S6M-profiles/gongbu/ with infra-health-check skill.
    """
    print("\n" + "="*60)
    print("A1: gongbu self-dept skill loading (infra-health-check)")
    print("="*60)

    card_id = kanban_create(
        title="E2E-A1: gongbu self-dept infra-health-check",
        assignee="gongbu",
        skill="infra-health-check",
        body="你是一位基础设施运维专家。请列出你加载的所有 skills，并确认 infra-health-check 在其中。然后用一句话描述 infra-health-check 的用途。"
    )
    if not card_id:
        print("  ❌ FAIL: could not create card")
        return False

    print(f"  📋 Card: {card_id}")
    card = wait_for_completion(card_id)

    if not card or card_status(card) != "done":
        print(f"  ❌ FAIL: card not done (status={card_status(card)})")
        return False

    has_skill, evidence = verify_skill_loaded(card, "infra-health-check")
    print(f"  📝 Evidence (first 500 chars):\n  {evidence[:500]}")

    if has_skill:
        print(f"  ✅ PASS: gongbu loaded infra-health-check from self-dept")
        return True
    else:
        print(f"  ❌ FAIL: no evidence of infra-health-check in output")
        return False


def test_a2_engineer_cross_dept():
    """A2: engineer (no dept skills) cross-loads gongbu's infra-health-check.

    engineer does NOT have hermes-3S6M-profiles/engineer/ — must use M2CL.
    """
    print("\n" + "="*60)
    print("A2: engineer M2CL cross-dept loading (gongbu→infra-health-check)")
    print("="*60)

    card_id = kanban_create(
        title="E2E-A2: engineer cross-dept infra-health-check",
        assignee="engineer",
        skill="infra-health-check",
        body="你是兵部工程师(engineer)。你的部门没有自己的 dept skills 目录。请尝试加载 infra-health-check skill（这是工部 gongbu 的 skill）。列出你加载的所有 skills，确认 infra-health-check 是否加载成功，并说明它来自哪个部门。"
    )
    if not card_id:
        print("  ❌ FAIL: could not create card")
        return False

    print(f"  📋 Card: {card_id}")
    card = wait_for_completion(card_id)

    if not card or card_status(card) != "done":
        print(f"  ❌ FAIL: card not done (status={card_status(card)})")
        return False

    has_skill, evidence = verify_skill_loaded(card, "infra-health-check")
    print(f"  📝 Evidence (first 500 chars):\n  {evidence[:500]}")

    if has_skill:
        print(f"  ✅ PASS: engineer cross-loaded infra-health-check via M2CL")
        return True
    else:
        # M2CL might fail — that's an important finding
        print(f"  ⚠️  M2CL cross-dept loading might not be working — check evidence")
        return False


def test_a3_fallback_chain():
    """A3: Verify graceful degradation when skill doesn't exist.

    Create card with a clearly non-existent skill name, verify worker
    doesn't crash and reports the issue gracefully.
    """
    print("\n" + "="*60)
    print("A3: Fallback chain — non-existent skill (nonexistent-skill-xyz)")
    print("="*60)

    card_id = kanban_create(
        title="E2E-A3: fallback for nonexistent skill",
        assignee="gongbu",
        skill="nonexistent-skill-xyz-test-only",
        body="你被要求加载一个不存在的 skill: nonexistent-skill-xyz-test-only。请列出你实际加载的 skills，并说明这个 skill 是否加载成功。"
    )
    if not card_id:
        print("  ❌ FAIL: could not create card")
        return False

    print(f"  📋 Card: {card_id}")
    card = wait_for_completion(card_id)

    if not card or card_status(card) != "done":
        print(f"  ❌ FAIL: card not done (status={card_status(card)})")
        return False

    summary = card_summary(card)
    print(f"  📝 Summary (first 300 chars):\n  {summary[:300]}")

    # The card should complete (not crash) — worker should gracefully handle
    # missing skill. We accept either:
    # - "not found" / "unknown skill" / "not loaded" mention
    # - Or just normal completion with fallback skills only
    status_ok = card_status(card) == "done"
    not_crashed = "error" not in summary.lower() and "traceback" not in summary.lower()

    if status_ok and not_crashed:
        print(f"  ✅ PASS: graceful degradation — worker completed without crash")
        return True
    else:
        print(f"  ❌ FAIL: worker crashed or errored on missing skill")
        return False


def main():
    results = {}

    results["A1"] = test_a1_gongbu_self_dept()
    results["A2"] = test_a2_engineer_cross_dept()
    results["A3"] = test_a3_fallback_chain()

    print("\n" + "="*60)
    print("RESULTS SUMMARY")
    print("="*60)
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")

    all_pass = all(results.values())
    print(f"\n  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
