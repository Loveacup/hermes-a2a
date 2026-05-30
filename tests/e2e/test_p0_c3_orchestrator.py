#!/usr/bin/env python3
"""E2E Phase 1C: orchestrator_router — VoteTally + deadlock guard + debate (v2).

Tests:
  C1: VoteTally — post real VOTE_FOR/VOTE_AGAINST/ABSTAIN comments to a
      live kanban task, classify each, and verify ``aggregate_votes`` and
      ``majority`` match the expected tally.
  C2: Deadlock guard — two real kanban cards depending on each other reach a
      terminal state; we also drive the kind-side deadlock detector with three
      same-kind comments to verify ``detect_deadlock`` fires.
  C3: Five-turn debate — post one comment per DCI turn through the live CLI,
      classify each, and assert at least four distinct kinds are recorded
      in ``a2a_comment_kinds`` for the same task (thread integrity).

Rewrite rationale (replaces v1):
  v1 asked a single regent LLM worker to "orchestrate a three-province
  debate" in one chat turn, then waited 300s for status=done. The worker
  has no multi-agent tools — it just produces text. Even successful runs
  wouldn't produce A2A protocol calls; the test asked the wrong layer.

  v2 mirrors the B v2 approach: drive the *real* kanban write path
  (`hermes kanban create` / `hermes kanban comment`) so every artifact
  hits production sqlite, then run the orchestrator primitives against the
  resulting thread. No LLM in the loop; orchestrator behaviour is the
  contract under test.

Prerequisites:
  - Scheme D migration applied (001_a2a_comment_kinds.sql)
  - kanban dispatcher running so C2 cards reach terminal state
  - kanban.db integrity ok

Usage:
  python tests/e2e/test_p0_c3_orchestrator.py
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

def _kanban_db() -> Path:
    """Lazy resolution — HERMES_HOME is set by pytest fixtures, not at import time."""
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "kanban.db"


TIMEOUT_PER_CARD = 240  # C2 spawns real LLM workers
POLL_INTERVAL = 10


# ─── CLI helpers ────────────────────────────────────────────────

def hermes(*args, timeout=30):
    p = subprocess.run(["hermes", *args],
                       capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def kanban_create(title: str, assignee: str = "default",
                  body: str = "", skill: str | None = None) -> str | None:
    args = ["kanban", "create", title, "--assignee", assignee,
            "--body", body or f"{title} — anchor", "--json"]
    if skill:
        args += ["--skill", skill]
    stdout, stderr, rc = hermes(*args)
    if rc != 0:
        print(f"  ❌ kanban create failed: {stderr}")
        return None
    try:
        return json.loads(stdout).get("id")
    except json.JSONDecodeError:
        return None


def kanban_post_comment(task_id: str, body: str) -> bool:
    stdout, stderr, rc = hermes("kanban", "comment", task_id, body)
    if rc != 0:
        print(f"  ❌ kanban comment failed: {stderr}")
        return False
    return True


def kanban_show(task_id: str) -> dict | None:
    stdout, _, rc = hermes("kanban", "show", task_id, "--json")
    if rc != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def kanban_archive(task_id: str) -> None:
    hermes("kanban", "archive", task_id, timeout=15)


def card_status(card: dict | None) -> str:
    return (card or {}).get("task", {}).get("status", "unknown")


def latest_comment_id_for(conn, task_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM task_comments WHERE task_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return row[0] if row else None


def wait_for_completion(card_id: str, timeout: int = TIMEOUT_PER_CARD) -> dict | None:
    start = time.time()
    last_status = None
    while time.time() - start < timeout:
        card = kanban_show(card_id)
        if card:
            status = card_status(card)
            if status != last_status:
                print(f"  [{int(time.time() - start)}s] {card_id}: {status}")
                last_status = status
            if status in ("done", "blocked", "cancelled"):
                return card
        time.sleep(POLL_INTERVAL)
    print(f"  ⏰ TIMEOUT after {timeout}s — last status: {last_status}")
    return kanban_show(card_id)


def post_classify_record(conn, task_id: str,
                          body: str, expected_kind: ck.CommentKind):
    """Post body, classify, record kind. Returns the comment row id."""
    if not kanban_post_comment(task_id, body):
        return None
    time.sleep(0.3)
    cid = latest_comment_id_for(conn, task_id)
    if cid is None:
        return None
    classified = cls.classify(body)
    if classified != expected_kind:
        print(f"      ❌ classifier returned {classified}, expected {expected_kind}")
        return None
    ck.record_kind(conn, comment_id=cid, kind=classified, task_id=task_id)
    return cid


# ─── C1: VoteTally ───────────────────────────────────────────────

def test_c1_vote_tally():
    print("\n" + "=" * 60)
    print("C1: VoteTally — vote aggregation over real comments")
    print("=" * 60)
    if not _kanban_db().exists():
        print(f"  ❌ kanban.db missing at {_kanban_db()}")
        return False

    conn = sqlite3.connect(str(_kanban_db()))
    conn.row_factory = sqlite3.Row
    tid = kanban_create("E2E-C1-v2: VoteTally anchor", assignee="default")
    if not tid:
        return False
    print(f"  📋 Anchor: {tid}")

    try:
        # Three FOR, two AGAINST, one ABSTAIN → expected for=3, against=2,
        # abstain=1, majority='for'.
        ballot = [
            ("[VOTE_FOR] 翰林院支持采纳 hermes-a2a-preview",
             ck.CommentKind.VOTE_FOR),
            ("[VOTE_FOR] 工部支持，运维负担可接受",
             ck.CommentKind.VOTE_FOR),
            ("[VOTE_FOR] 户部支持，本季预算够",
             ck.CommentKind.VOTE_FOR),
            ("[VOTE_AGAINST] 兵部反对，安全风险未审清",
             ck.CommentKind.VOTE_AGAINST),
            ("[VOTE_AGAINST] 御史反对，缺少回滚预案",
             ck.CommentKind.VOTE_AGAINST),
            ("[ABSTAIN] 史馆弃权，证据不足",
             ck.CommentKind.ABSTAIN),
        ]

        for body, kind in ballot:
            cid = post_classify_record(conn, tid, body, kind)
            if cid is None:
                print(f"      ❌ failed to record ballot: {body[:50]}")
                return False

        thread = ck.get_thread(conn, tid)
        tally = orx.aggregate_votes(thread)
        print(f"  📊 tally: for={tally.for_} against={tally.against} "
              f"abstain={tally.abstain} total={tally.total}")
        print(f"  🏆 majority: {tally.majority}")

        ok = (tally.for_ == 3 and tally.against == 2
              and tally.abstain == 1 and tally.majority == "for")
        if ok:
            print(f"  ✅ PASS: tally matches expected (3:2:1, majority=for)")
        else:
            print(f"  ❌ FAIL: tally mismatch")
        return ok
    finally:
        kanban_archive(tid)
        conn.close()


# ─── C2: Deadlock guard ──────────────────────────────────────────

def test_c2_deadlock_guard():
    """Two angles:
      (i) detect_deadlock fires after three same-kind, non-converging comments
      (ii) two real LLM cards in a circular-dep prompt reach a terminal state
    Either branch passes counts the test as ✅; both passing is the strong form.
    """
    print("\n" + "=" * 60)
    print("C2: Deadlock guard — detect_deadlock + live-cards terminal state")
    print("=" * 60)
    if not _kanban_db().exists():
        print(f"  ❌ kanban.db missing at {_kanban_db()}")
        return False

    conn = sqlite3.connect(str(_kanban_db()))
    conn.row_factory = sqlite3.Row

    # ── (i) detect_deadlock against an anchor thread ─────────────
    tid = kanban_create("E2E-C2-v2: deadlock guard anchor", assignee="default")
    if not tid:
        return False
    print(f"  📋 Anchor: {tid}")

    converging = False
    fired = False
    try:
        # Seed with a PROPOSE so the thread is non-vacuous, then three
        # CHALLENGE in a row — the canonical stall pattern.
        post_classify_record(conn, tid,
                             "[PROPOSE] 建议改为 webhook 即时唤醒",
                             ck.CommentKind.PROPOSE)
        for i in range(3):
            post_classify_record(conn, tid,
                                 f"[CHALLENGE] 反对第 {i+1} 次：仍有同样问题",
                                 ck.CommentKind.CHALLENGE)
        thread = ck.get_thread(conn, tid)
        fired = orx.detect_deadlock(thread)
        print(f"  🚥 detect_deadlock (3 CHALLENGE in a row): {fired}")

        if fired:
            resp = orx.deadlock_response(thread) if hasattr(
                orx, "deadlock_response") else None
            if resp is not None:
                print(f"  📡 deadlock_response → {resp.target_profile} "
                      f"({resp.reason})")

        # Then a CONCEDE should break the deadlock
        post_classify_record(conn, tid,
                             "[CONCEDE] 接受方案 B，停止僵持",
                             ck.CommentKind.CONCEDE)
        thread = ck.get_thread(conn, tid)
        converging = not orx.detect_deadlock(thread)
        print(f"  🟢 deadlock cleared after CONCEDE: {converging}")
    finally:
        kanban_archive(tid)
        conn.close()

    closed_form_ok = fired and converging
    print(f"  → closed-form deadlock check: "
          f"{'✅' if closed_form_ok else '❌'}")

    # ── (ii) live cards: keep the original sanity test ───────────
    card_a = kanban_create(
        "E2E-C2-v2: live deadlock parent", assignee="gongbu",
        skill="infra-health-check",
        body="这是死锁测试。card B 依赖你，你也依赖 card B。报告是否检测到循环。",
    )
    card_b = kanban_create(
        "E2E-C2-v2: live deadlock child", assignee="engineer",
        skill="infra-health-check",
        body="这是死锁测试。card A 依赖你，你也依赖 card A。报告是否检测到循环。",
    )
    if not card_a or not card_b:
        print("  ⚠️  Could not create live cards; closed-form result stands.")
        return closed_form_ok

    print(f"  📋 Live cards: A={card_a}, B={card_b}")
    a_data = wait_for_completion(card_a)
    b_data = wait_for_completion(card_b)
    status_a = card_status(a_data)
    status_b = card_status(b_data)
    print(f"  Result: A={status_a}, B={status_b}")

    live_ok = status_a in ("done", "blocked") and status_b in ("done", "blocked")
    print(f"  → live-cards reached terminal: {'✅' if live_ok else '❌'}")

    return closed_form_ok and live_ok


# ─── C3: Five-turn debate ────────────────────────────────────────

def test_c3_comprehensive_debate():
    """C3: drive a five-turn debate via real kanban writes, then assert the
    thread carries the expected kinds. Replaces the v1 attempt to have a
    single LLM worker simulate three-province orchestration.
    """
    print("\n" + "=" * 60)
    print("C3: five-turn debate — thread integrity over a2a_comment_kinds")
    print("=" * 60)
    if not _kanban_db().exists():
        print(f"  ❌ kanban.db missing at {_kanban_db()}")
        return False

    conn = sqlite3.connect(str(_kanban_db()))
    conn.row_factory = sqlite3.Row
    tid = kanban_create("E2E-C3-v2: A2A evolution debate anchor",
                        assignee="default")
    if not tid:
        return False
    print(f"  📋 Anchor: {tid}")

    # Five turns drawn from the production-shape script used by
    # tests/e2e/test_dci_pipeline.py, but routed through the real
    # `hermes kanban comment` write path so the bypass table is exercised
    # through the bridge, not directly seeded.
    script = [
        ("[PROPOSE] 应当采用 webhook 即时唤醒，比轮询延迟低且省 token",
         ck.CommentKind.PROPOSE),
        ("根据 G²CP 论文 arxiv 2602.13370 的数据显示，结构化通信减少 73% token",
         ck.CommentKind.EVIDENCE_FOR),
        ("我质疑这一论点：webhook 需公网入口，内网穿透增加 SSRF 攻击面",
         ck.CommentKind.CHALLENGE),
        ("[SYNTHESIZE] 综合各方意见，先 mTLS 包裹的 webhook，再评估全量",
         ck.CommentKind.SYNTHESIZE),
        ("[CONCEDE] 接受 mTLS + 灰度方案",
         ck.CommentKind.CONCEDE),
    ]

    try:
        for body, expected_kind in script:
            cid = post_classify_record(conn, tid, body, expected_kind)
            if cid is None:
                print(f"      ❌ failed to record: {body[:50]}")
                return False

        thread = ck.get_thread(conn, tid)
        print(f"  💬 thread length: {len(thread)}")

        kinds_found = sorted({e.kind for e in thread})
        print(f"  🏷️  DCI kinds in thread: {kinds_found}")

        required = {
            ck.CommentKind.PROPOSE.value,
            ck.CommentKind.EVIDENCE_FOR.value,
            ck.CommentKind.CHALLENGE.value,
            ck.CommentKind.SYNTHESIZE.value,
        }
        present_set = set(kinds_found)
        missing = required - present_set

        thread_ok = len(thread) == len(script)
        kinds_ok = not missing and len(present_set) >= 4

        if thread_ok and kinds_ok:
            print(f"  ✅ PASS: 5-turn debate with {len(present_set)} kinds")
            return True
        print(f"  ❌ FAIL: thread_ok={thread_ok} kinds_ok={kinds_ok} "
              f"missing={sorted(missing) if missing else 'none'}")
        return False
    finally:
        kanban_archive(tid)
        conn.close()


# ─── runner ──────────────────────────────────────────────────────

def main():
    results = {
        "C1": test_c1_vote_tally(),
        "C2": test_c2_deadlock_guard(),
        "C3": test_c3_comprehensive_debate(),
    }
    print("\n" + "=" * 60)
    print("PHASE 1C RESULTS (v2 — orchestrator over real kanban writes)")
    print("=" * 60)
    for name, passed in results.items():
        print(f"  {name}: {'✅ PASS' if passed else '❌ FAIL'}")
    all_pass = all(results.values())
    print(f"\n  Overall: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
