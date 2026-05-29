"""E2E: end-to-end DCI pipeline on a simulated three-province debate.

Scope:
  This is the closed-form variant of test_p0_c3_orchestrator.py: it drives
  the full pipeline (comment write → classifier → backfill → bypass table →
  view → orchestrator decision) WITHOUT calling a real LLM. The LLM is
  replaced by a deterministic five-turn script written into task_comments
  directly so the test runs in <2s and is reproducible.

Pipeline under test:

  agent writes free-text comment           ──► task_comments  (upstream)
                                              │
  comment_kind_classifier.classify(body)  ◄──┘
                                              │
  comment_kind_backfill.backfill(task_id)    │
        │                                     │
        ▼                                     │
  record_kind() writes a2a_comment_kinds     │
        │                                     │
        ▼                                     │
  a2a_thread_view (LEFT JOIN)  ◄─────────────┘
        │
        ▼
  orchestrator_router.route_comment / aggregate_votes / detect_deadlock

Plan: Phase B-Discovery → identified the gap (no comment→kind bridge in
production) and built classifier + backfill modules. This file is the
end-to-end contract proving the bridge connects.
"""
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "core"))

import comment_kind as ck                # noqa: E402
import comment_kind_classifier as cls    # noqa: E402
import comment_kind_backfill as bf       # noqa: E402
import orchestrator_router as orx        # noqa: E402

MIGRATION_SQL = ROOT / "s6m-config" / "migrations" / "001_a2a_comment_kinds.sql"


# Five-turn deterministic debate. Mix of explicit prefix and heuristic
# (no prefix). Each turn is (author, body, expected_kind).
DEBATE_SCRIPT: list[tuple[str, str, ck.CommentKind]] = [
    # 翰林院 PROPOSE — explicit prefix path
    ("hanlinyuan",
     "[PROPOSE] 应当采用 webhook 即时唤醒模式，比当前轮询模型在延迟与成本上都更优",
     ck.CommentKind.PROPOSE),

    # 工部 EVIDENCE_FOR — heuristic via "根据.*论文" + "数据显示"
    ("gongbu",
     "根据 G²CP 论文 arxiv 2602.13370 的数据显示，结构化通信能减少 73% token 消耗",
     ck.CommentKind.EVIDENCE_FOR),

    # 兵部 CHALLENGE — heuristic via "我质疑"
    ("engineer",
     "我质疑这一论点：webhook 需要公网入口，内网穿透引入 SSRF 攻击面",
     ck.CommentKind.CHALLENGE),

    # 御史 REFINE — heuristic via "建议改成" + "更好的做法"
    ("reviewer",
     "建议改成混合方案：仅对受信源签名后开放 webhook，其余继续轮询",
     ck.CommentKind.REFINE),

    # 太子 SYNTHESIZE — explicit prefix path
    ("regent",
     "[SYNTHESIZE] 综合各方意见，先评估 hermes-a2a-preview PR #11025 的实际落地成本再决",
     ck.CommentKind.SYNTHESIZE),
]


def setup_db(path: Path) -> sqlite3.Connection:
    """Init an empty kanban-shaped db and apply the bypass migration.

    We don't shell out to hermes here — the test is about the bridge
    above the upstream table, so we construct a minimal task_comments
    shape that matches the production schema.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX idx_comments_task ON task_comments(task_id, created_at);
    """)
    conn.executescript(MIGRATION_SQL.read_text())
    conn.commit()
    return conn


def seed_debate(conn: sqlite3.Connection, task_id: str) -> list[int]:
    """Write the deterministic five-turn script into task_comments."""
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) "
        "VALUES (?, ?, ?, strftime('%s','now'))",
        (task_id, "A2A protocol evolution — three-province debate", "ready"),
    )
    cids = []
    base = int(time.time())
    for i, (author, body, _) in enumerate(DEBATE_SCRIPT):
        # Spread times so ORDER BY created_at, id is deterministic
        cur = conn.execute(
            "INSERT INTO task_comments (task_id, author, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, author, body, base + i),
        )
        cids.append(cur.lastrowid)
    conn.commit()
    return cids


# ── Assertions ─────────────────────────────────────────────────


def step_classifier_unit():
    """Stand-alone classifier sanity: each scripted line maps to the
    expected kind. This isolates classifier bugs from backfill / view bugs."""
    failures = []
    for author, body, want in DEBATE_SCRIPT:
        got = cls.classify(body)
        if got != want:
            failures.append((author, want, got, body[:60]))
    if failures:
        for f in failures:
            print(f"  ❌ classifier: {f[0]} want={f[1]} got={f[2]}  body={f[3]!r}")
        return False
    print(f"  ✅ classifier: {len(DEBATE_SCRIPT)}/{len(DEBATE_SCRIPT)} lines mapped")
    return True


def step_backfill(conn, task_id):
    """Backfill must classify every line and write into the bypass table."""
    result = bf.backfill_task(conn, task_id)
    if result.classified != len(DEBATE_SCRIPT):
        print(f"  ❌ backfill: classified {result.classified}, expected {len(DEBATE_SCRIPT)}")
        return False, result
    if result.defaulted > 0:
        print(f"  ❌ backfill: {result.defaulted} fell to default — classifier blind spots")
        return False, result
    print(f"  ✅ backfill: classified={result.classified} skipped={result.skipped}")
    print(f"     by_kind: {result.by_kind}")
    return True, result


def step_view_join(conn, task_id):
    """a2a_thread_view returns one ordered row per scripted comment, every
    row has has_a2a_record=1 and matches the script's expected kind."""
    rows = conn.execute(
        "SELECT comment_id, author, kind, has_a2a_record "
        "FROM a2a_thread_view WHERE task_id = ? ORDER BY created_at, comment_id",
        (task_id,),
    ).fetchall()
    if len(rows) != len(DEBATE_SCRIPT):
        print(f"  ❌ view: {len(rows)} rows, expected {len(DEBATE_SCRIPT)}")
        return False
    for row, (author, _, want) in zip(rows, DEBATE_SCRIPT):
        if row["has_a2a_record"] != 1:
            print(f"  ❌ view: row {row['comment_id']} has_a2a_record=0 after backfill")
            return False
        if row["author"] != author:
            print(f"  ❌ view: row {row['comment_id']} author={row['author']} want={author}")
            return False
        if row["kind"] != want.value:
            print(f"  ❌ view: row {row['comment_id']} kind={row['kind']} want={want.value}")
            return False
    print(f"  ✅ view: {len(rows)} rows fully classified, all has_a2a_record=1")
    return True


def step_orchestrator_routing(conn, task_id):
    """Each kind in the script routes per ROUTE_BY_KIND:
       PROPOSE→None, EVIDENCE_FOR→archivist, CHALLENGE→regent,
       REFINE→None, SYNTHESIZE→regent."""
    thread = ck.get_thread(conn, task_id)
    expectations = [
        (None, False),         # propose
        ("archivist", False),  # evidence_for
        ("regent", False),     # challenge
        (None, False),         # refine
        ("regent", False),     # synthesize
    ]
    for entry, (want_target, want_agg) in zip(thread, expectations):
        r = orx.route_comment(entry)
        if want_target is None and want_agg is False:
            if r is not None:
                print(f"  ❌ routing: kind={entry.kind} expected None, got {r}")
                return False
            continue
        if r is None:
            print(f"  ❌ routing: kind={entry.kind} expected target={want_target}, got None")
            return False
        if r.target_profile != want_target or r.is_aggregator != want_agg:
            print(f"  ❌ routing: kind={entry.kind} got target={r.target_profile} agg={r.is_aggregator}")
            return False
    print(f"  ✅ routing: 5/5 decisions correct (CHALLENGE→regent, SYNTHESIZE→regent confirmed)")
    return True


def step_deadlock_and_votes(conn, task_id):
    """Five distinct kinds → no deadlock. No VOTE_* in script → tally 0:0:0."""
    thread = ck.get_thread(conn, task_id)
    deadlocked = orx.detect_deadlock(thread, window=3)
    tally = orx.aggregate_votes(thread)
    if deadlocked:
        print(f"  ❌ deadlock: false-positive on a heterogeneous thread")
        return False
    if (tally.for_, tally.against, tally.abstain) != (0, 0, 0):
        print(f"  ❌ vote tally: expected 0:0:0, got {tally}")
        return False
    print(f"  ✅ deadlock=False, vote tally=0:0:0 (no votes scripted)")
    return True


def step_upstream_isolation(conn, task_id):
    """task_comments must be unchanged by the backfill — only
    a2a_comment_kinds got new rows. This is the Scheme D zero-touch
    contract."""
    n_upstream = conn.execute(
        "SELECT COUNT(*) FROM task_comments WHERE task_id = ?", (task_id,),
    ).fetchone()[0]
    n_bypass = conn.execute(
        "SELECT COUNT(*) FROM a2a_comment_kinds WHERE task_id = ?", (task_id,),
    ).fetchone()[0]
    # Schema integrity: PRAGMA table_info(task_comments) has exactly 5 cols
    cols = [r[1] for r in conn.execute("PRAGMA table_info(task_comments)")]
    expected_cols = {"id", "task_id", "author", "body", "created_at"}
    if set(cols) != expected_cols:
        print(f"  ❌ isolation: upstream cols drifted — got {cols}")
        return False
    if n_upstream != len(DEBATE_SCRIPT) or n_bypass != len(DEBATE_SCRIPT):
        print(f"  ❌ isolation: upstream={n_upstream} bypass={n_bypass}, expected both {len(DEBATE_SCRIPT)}")
        return False
    print(f"  ✅ isolation: upstream schema intact, "
          f"task_comments={n_upstream}, a2a_comment_kinds={n_bypass}")
    return True


def main():
    print("=" * 64)
    print("DCI PIPELINE E2E — three-province debate (5 turns, no LLM)")
    print("=" * 64)

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "kanban.db"
        conn = setup_db(db_path)
        try:
            task_id = "t-dci-e2e-1"

            print("\n[1/6] classifier unit")
            r1 = step_classifier_unit()

            seed_debate(conn, task_id)
            print(f"\n[2/6] seeded {len(DEBATE_SCRIPT)} comments into upstream task_comments")

            print("\n[3/6] backfill bridge")
            r3, _ = step_backfill(conn, task_id)

            print("\n[4/6] a2a_thread_view join")
            r4 = step_view_join(conn, task_id)

            print("\n[5/6] orchestrator routing")
            r5 = step_orchestrator_routing(conn, task_id)

            print("\n[6/6] deadlock + vote tally + upstream isolation")
            r6a = step_deadlock_and_votes(conn, task_id)
            r6b = step_upstream_isolation(conn, task_id)

            all_pass = all([r1, r3, r4, r5, r6a, r6b])

            print("\n" + "=" * 64)
            print(f"RESULT: {'✅ ALL 6 STAGES PASSED' if all_pass else '❌ FAILURE'}")
            print("=" * 64)
            return 0 if all_pass else 1
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
