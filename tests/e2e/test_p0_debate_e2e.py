#!/usr/bin/env python3
"""E2E P0: 三省辩论 — DCI full pipeline.

A real debate exercises the full chain:
  1. regent creates an anchor kanban card (topic prompt)
  2. Each of {hanlinyuan, gongbu, engineer} posts a labeled comment via A2A
     (i.e., a real LLM task asks the worker to call `kanban_comment` with the
     pre-baked body).  The worker's `task_handler.handle_task` then triggers
     `_ensure_comment_kind_backfill`, which classifies + writes the DCI kind
     into `a2a_comment_kinds` (Scheme D bypass table) — that's the production
     write path.
  3. Verify (closed-form, no LLM):
       - a2a_comment_kinds got rows with the expected kinds
       - comment_kind_classifier maps each body to the expected kind
       - orchestrator_router.route_comment hands each kind to the expected
         target profile (ASK→hanlinyuan, CHALLENGE→regent, EVIDENCE_FOR→
         archivist)
       - a2a_thread_view returns the merged thread (integrity)
  4. Optional 4th turn: send a SYNTHESIZE A2A task to regent and confirm
     the synthesizing comment also lands in the bypass table.

Why not run the orchestrator daemon itself?  The user-facing contract here
is data-plane correctness: when comments flow in, the table fills and the
router decides who acts next.  Driving an actual auto-dispatcher loop adds
flake without sharpening the assertions.

Prerequisites:
  - 16/16 A2A healthy (verified via /health before each round)
  - Scheme D migration applied (a2a_comment_kinds + a2a_thread_view exist)
  - HOME=/Users/alexcai HERMES_HOME=/Users/alexcai/.hermes

Usage:
  HOME=/Users/alexcai HERMES_HOME=/Users/alexcai/.hermes \
    /Users/alexcai/.hermes/hermes-agent/venv/bin/python3 \
    tests/e2e/test_p0_debate_e2e.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "core"))

import comment_kind as ck              # noqa: E402
import comment_kind_classifier as cls  # noqa: E402
import orchestrator_router as orx      # noqa: E402

# ─── env / fixtures ─────────────────────────────────────────────

KANBAN_DB = Path(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
) / "kanban.db"
TOKEN_PATH = Path("/Users/alexcai/.hermes/.a2a-token")

# A2A ports for the 4 participants
A2A_PORTS = {
    "regent": 8939,
    "hanlinyuan": 8702,
    "gongbu": 8898,
    "engineer": 8718,
    "archivist": 8804,
}

POLL_INTERVAL_S = 3
PER_TASK_TIMEOUT_S = 180

# (author_profile, prefix_body, expected_kind, expected_route_target)
DEBATE_TURNS = [
    (
        "hanlinyuan",
        "[ASK] 当前三省六部体系中有哪些跨部门通信场景？",
        ck.CommentKind.ASK,
        "hanlinyuan",
    ),
    (
        "gongbu",
        "[EVIDENCE_FOR] 实测数据：API Server 平均延迟 3-5s，subprocess 8-12s，差异显著",
        ck.CommentKind.EVIDENCE_FOR,
        "archivist",
    ),
    (
        "engineer",
        "[CHALLENGE] 16 个 API Server 常驻是否过度设计？资源占用应纳入评估",
        ck.CommentKind.CHALLENGE,
        "regent",
    ),
]


# ─── A2A helpers ────────────────────────────────────────────────

def _token() -> str:
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def a2a_post(profile: str, prompt: str) -> str | None:
    port = A2A_PORTS[profile]
    body = json.dumps({"message": {"text": prompt}}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/a2a/tasks",
        data=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read()).get("id")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"      ❌ a2a_post {profile}: {e}")
        return None


def a2a_poll(profile: str, tid: str, deadline: float) -> dict | None:
    port = A2A_PORTS[profile]
    url = f"http://127.0.0.1:{port}/a2a/tasks/{tid}"
    headers = {"Authorization": f"Bearer {_token()}"}
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            if data.get("status") in ("completed", "failed", "cancelled"):
                return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(POLL_INTERVAL_S)
    return None


# ─── kanban helpers ─────────────────────────────────────────────

def hermes(*args, timeout=30):
    p = subprocess.run(
        ["hermes", *args], capture_output=True, text=True, timeout=timeout
    )
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def kanban_create_anchor(title: str, body: str) -> str | None:
    stdout, stderr, rc = hermes(
        "kanban", "create", title,
        "--assignee", "regent",
        "--body", body,
        "--json",
    )
    if rc != 0:
        print(f"  ❌ kanban create failed: {stderr}")
        return None
    try:
        return json.loads(stdout).get("id")
    except json.JSONDecodeError:
        return None


def kanban_archive(task_id: str) -> None:
    hermes("kanban", "archive", task_id, timeout=15)


def latest_comment_for(conn, task_id: str) -> tuple[int, str, str] | None:
    row = conn.execute(
        "SELECT id, author, body FROM task_comments "
        "WHERE task_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return tuple(row) if row else None


def comments_with_body_like(conn, task_id: str, snippet: str) -> list[tuple]:
    return conn.execute(
        "SELECT id, author, body FROM task_comments "
        "WHERE task_id = ? AND body LIKE ? "
        "ORDER BY created_at, id",
        (task_id, f"%{snippet}%"),
    ).fetchall()


# ─── prompts ────────────────────────────────────────────────────

def post_comment_prompt(task_id: str, body: str) -> str:
    """Tight prompt — minimise LLM drift on the exact body string."""
    return (
        f"调用 kanban_comment 工具向任务 {task_id} 添加一条评论。\n"
        f"评论正文必须**逐字**为以下内容（不要改动、不要加任何前后缀、不要翻译）：\n"
        f"---\n{body}\n---\n"
        f"调用后只回复『已发送』两个字。"
    )


def synthesize_prompt(task_id: str) -> str:
    return (
        f"任务 {task_id} 是一个辩论卡。请阅读它的现有评论（kanban_get + 评论列表），"
        f"然后调用 kanban_comment 工具给该任务发一条 **[SYNTHESIZE]** 前缀的综合评论，"
        f"用 1-2 句话总结当前讨论的核心张力，并给出你作为太子的建议。"
        f"调用后只回复『已综合』。"
    )


# ─── checks ─────────────────────────────────────────────────────

def check_a2a_healthy() -> bool:
    print("\n[setup] checking 5 A2A participants healthy…")
    ok = True
    for p, port in A2A_PORTS.items():
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2
            )
            data = json.loads(resp.read())
            tag = "✓" if data.get("status") == "ok" else "✗"
            print(f"  {tag} {p}:{port}")
            ok = ok and (data.get("status") == "ok")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            print(f"  ✗ {p}:{port} unreachable")
            ok = False
    return ok


def check_bypass_table(conn) -> bool:
    ok = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='a2a_comment_kinds'"
    ).fetchone()
    if not ok:
        print("  ❌ a2a_comment_kinds table missing — apply migration first")
        return False
    print("  ✓ Scheme D bypass table present")
    return True


def check_classifier() -> bool:
    """Static check: classifier returns expected kinds for each body."""
    print("\n[verify-classifier] classify each prefixed body")
    all_ok = True
    for _, body, expected_kind, _ in DEBATE_TURNS:
        got = cls.classify(body)
        tag = "✓" if got == expected_kind else "✗"
        print(f"  {tag} {expected_kind.value:18s} ← {body[:50]}")
        if got != expected_kind:
            print(f"      got: {got.value if got else None}")
            all_ok = False
    return all_ok


def check_router() -> bool:
    """Static check: orchestrator routes each kind to the expected profile."""
    print("\n[verify-router] route_comment per kind")
    all_ok = True
    for _, body, kind, target in DEBATE_TURNS:
        # Build a synthetic ThreadEntry (router only inspects .kind)
        entry = ck.ThreadEntry(
            comment_id=0, task_id="synthetic", author="test",
            body=body, kind=kind.value, in_reply_to=None,
            metadata={}, created_at=0, has_a2a_record=True,
        )
        routing = orx.route_comment(entry)
        actual = routing.target_profile if routing else None
        tag = "✓" if actual == target else "✗"
        print(f"  {tag} {kind.value:18s} → {actual}  (expected {target})")
        if actual != target:
            all_ok = False
    return all_ok


# ─── main flow ──────────────────────────────────────────────────

def run() -> int:
    print("=" * 72)
    print("P0 E2E — 三省辩论 (DCI full pipeline)")
    print("=" * 72)

    if not check_a2a_healthy():
        print("❌ A2A participants not healthy — aborting")
        return 2
    if not KANBAN_DB.is_file():
        print(f"❌ kanban.db missing: {KANBAN_DB}")
        return 2

    conn = sqlite3.connect(str(KANBAN_DB))
    if not check_bypass_table(conn):
        conn.close()
        return 2

    # ── Closed-form verifications first (fast, no LLM) ──────────
    classifier_ok = check_classifier()
    router_ok = check_router()

    # ── Phase A: anchor card ────────────────────────────────────
    print("\n[phase-A] regent creates anchor kanban card")
    topic_title = "辩论：API Server 全推广后 A2A 价值剩余"
    topic_body = (
        "讨论：三省六部体系中，API Server 全推广后 A2A 的价值还剩什么？"
        "请各部门以 [ASK] / [EVIDENCE_FOR] / [CHALLENGE] / [SYNTHESIZE] 前缀发言。"
    )
    anchor_id = kanban_create_anchor(topic_title, topic_body)
    if not anchor_id:
        conn.close()
        return 1
    print(f"  ✓ anchor task created: {anchor_id}")

    # ── Phase B: 3 real-A2A comment-posting turns ───────────────
    print("\n[phase-B] each participant posts a labeled comment via A2A")
    posted: list[dict] = []
    for idx, (profile, body, kind, target) in enumerate(DEBATE_TURNS, 1):
        print(f"\n  --- turn {idx}/3 : {profile} posts {kind.value} ---")
        prompt = post_comment_prompt(anchor_id, body)
        tid = a2a_post(profile, prompt)
        if not tid:
            posted.append({"profile": profile, "ok": False, "reason": "post_failed"})
            continue
        data = a2a_poll(profile, tid, deadline=time.time() + PER_TASK_TIMEOUT_S)
        if data is None:
            posted.append({"profile": profile, "ok": False, "reason": "poll_timeout", "task_id": tid})
            continue
        artifact = data.get("artifact") or {}
        mode = artifact.get("mode")
        status = data.get("status")
        dur = artifact.get("duration_s")
        print(f"      status={status} mode={mode} dur={dur}s")
        # We don't require the LLM to succeed — what we ultimately verify is
        # whether the comment ended up in task_comments + a2a_comment_kinds.
        posted.append({
            "profile": profile, "task_id": tid, "a2a_status": status,
            "mode": mode, "duration_s": dur, "ok": True,
        })

    # Let backfill catch up
    print("\n  …giving task_handler backfill ~5s to settle…")
    time.sleep(5)

    # ── Phase C: verify writes landed ───────────────────────────
    print("\n[phase-C] verify writes landed in task_comments + a2a_comment_kinds")
    per_turn_results = []
    for (profile, body, kind, target), p in zip(DEBATE_TURNS, posted):
        # Pull rows whose body matches a small invariant snippet (prefix tag).
        # The exact body should be there if the LLM obeyed; if it embedded
        # additional text we still match on the bracket prefix.
        snippet = body.split("]", 1)[0] + "]"  # e.g. "[ASK]"
        rows = comments_with_body_like(conn, anchor_id, snippet)
        if not rows:
            print(f"  ✗ {profile}: no comment row with snippet {snippet!r}")
            per_turn_results.append((profile, False, "no_comment_row"))
            continue
        cid, author, comment_body = rows[-1]
        kind_row = conn.execute(
            "SELECT kind FROM a2a_comment_kinds WHERE comment_id = ?",
            (cid,),
        ).fetchone()
        actual_kind = kind_row[0] if kind_row else None
        kind_ok = actual_kind == kind.value
        tag = "✓" if kind_ok else "✗"
        print(f"  {tag} {profile}: cid={cid} author={author} kind_row={actual_kind} "
              f"(expected {kind.value})")
        per_turn_results.append((profile, kind_ok, actual_kind))

    # ── Phase D: thread integrity ───────────────────────────────
    print("\n[phase-D] a2a_thread_view integrity")
    thread = ck.get_thread(conn, anchor_id)
    print(f"  thread has {len(thread)} entries for {anchor_id}")
    kinds_in_thread = {e.kind for e in thread}
    expected_kinds = {k.value for _, _, k, _ in DEBATE_TURNS}
    missing = expected_kinds - kinds_in_thread
    thread_ok = not missing
    if missing:
        print(f"  ✗ thread missing kinds: {sorted(missing)}")
    else:
        print(f"  ✓ all 3 debate kinds present in thread")

    # Show one entry per kind for visual confirmation
    seen: set[str] = set()
    for e in thread:
        if e.kind in expected_kinds and e.kind not in seen:
            print(f"    [{e.kind:18s}] cid={e.comment_id} a2a={e.has_a2a_record} "
                  f"body={e.body[:60]}")
            seen.add(e.kind)

    # ── Phase E: live router on real thread entries ─────────────
    print("\n[phase-E] live route_comment over real thread entries")
    live_route_ok = True
    for _, _, kind, target in DEBATE_TURNS:
        entry = next((e for e in thread if e.kind == kind.value), None)
        if entry is None:
            print(f"  ✗ no live entry for {kind.value}")
            live_route_ok = False
            continue
        routing = orx.route_comment(entry)
        actual = routing.target_profile if routing else None
        tag = "✓" if actual == target else "✗"
        print(f"  {tag} {kind.value:18s} → {actual} (expected {target})")
        if actual != target:
            live_route_ok = False

    # ── Cleanup ────────────────────────────────────────────────
    kanban_archive(anchor_id)
    conn.close()

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("DEBATE E2E SUMMARY")
    print("=" * 72)
    print(f"  classifier (static):     {'✅' if classifier_ok else '❌'}")
    print(f"  router (static):         {'✅' if router_ok else '❌'}")
    print(f"  3 A2A comment posts:     {sum(1 for r in posted if r['ok'])}/3")
    print(f"  bypass-table writes:     "
          f"{sum(1 for _, ok, _ in per_turn_results if ok)}/3")
    print(f"  thread integrity:        {'✅' if thread_ok else '❌'}")
    print(f"  router (live thread):    {'✅' if live_route_ok else '❌'}")

    overall = (
        classifier_ok and router_ok and thread_ok and live_route_ok
        and all(ok for _, ok, _ in per_turn_results)
    )
    print(f"\n  Overall: {'✅ PASS' if overall else '❌ FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(run())
