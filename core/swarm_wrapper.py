"""W4: DCI-aware kanban swarm wrapper (orchestrator_router 拓扑层迁移).

把 hermes v0.15.x 原生的 `kanban swarm` 当成拓扑层（root → workers → verifier
→ synthesizer），在 verifier 阶段钩入本仓 DCI 决策核心：

  - core/orchestrator_router.py 的 ROUTE_BY_KIND / VoteTally / deadlock
  - core/comment_kind.py 的 ThreadEntry / get_thread

策略上明确分工：
  - 拓扑创建走 hermes_cli.kanban_swarm.create_swarm，复用原生 idempotency、
    metadata、blackboard 约定。这一层属于 hermes-agent，不在本仓维护。
  - 闸门判断走 decide_gate，给 verifier 一个可机器消费的 GateVerdict
    （pass/block、reason、tally、deadlocked、route_hint）。

这两件事故意不耦合：拓扑由 hermes 写库，决策由本仓函数计算。任何 hermes
upstream 升级（比如换出 ks.create_swarm）只需要在 _create_swarm_native 这
一处适配；决策核心保持纯函数，可独测、可重放。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from hermes_cli import kanban_swarm as ks

from comment_kind import CommentKind, ThreadEntry, get_thread
from orchestrator_router import (
    Routing,
    VoteTally,
    aggregate_votes,
    deadlock_response,
    detect_deadlock,
    route_comment,
)


# ─── public DTOs ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkerSpec:
    """Wrapper-friendly worker descriptor; folds into ks.SwarmWorkerSpec."""
    profile: str
    title: str
    skills: list[str] | None = None


@dataclass(frozen=True)
class GateVerdict:
    """Recommendation passed to the verifier profile.

    Verifier 收到后选择 `complete_task(metadata={"gate": gate, ...})` 或者
    保留 in_progress 等待补证据。tally/deadlocked/route_hint 作为审计字段。
    """
    gate: str                       # "pass" | "block"
    reason: str
    tally: VoteTally
    deadlocked: bool
    route_hint: Routing | None


# 在 verifier 任务正文末尾追加的协议提示，告诉它怎么消费 GateVerdict。
_VERIFIER_BODY_SUFFIX = (
    "\n\n## DCI gate protocol (W4 swarm wrapper)\n"
    "- `swarm_wrapper.decide_gate(conn, root_id)` 给出 GateVerdict 推荐。\n"
    "- pass 触发条件 (二选一):\n"
    "  (a) VoteTally.majority == 'for'\n"
    "  (b) 末条 entry.kind ∈ {SYNTHESIZE, CONCEDE}\n"
    "- 否则 block；deadlock 自动 escalate 给 regent。\n"
    "- 最终落库时仍按 hermes 原生约定写 metadata={\"gate\": \"pass\"}。\n"
)


# ─── topology layer (kanban swarm) ──────────────────────────────────────

def _to_ks_workers(workers: Iterable[WorkerSpec]) -> list[ks.SwarmWorkerSpec]:
    out: list[ks.SwarmWorkerSpec] = []
    for w in workers:
        out.append(ks.SwarmWorkerSpec(
            profile=w.profile,
            title=w.title,
            body=w.title,
            skills=list(w.skills or []),
        ))
    return out


def create_swarm(
    conn: sqlite3.Connection,
    *,
    goal: str,
    workers: Iterable[WorkerSpec],
    verifier: str,
    synthesizer: str,
    tenant: str | None = None,
    created_by: str = "swarm-orchestrator",
    priority: int = 0,
    idempotency_key: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create (or simulate) a DCI-gated swarm graph.

    dry_run=True 返回拓扑骨架，不触 DB；便于 CLI/CI 在不写库的情况下
    回归 W4 接口。dry_run=False 时落库走 hermes_cli.kanban_swarm。
    """
    worker_list = list(workers)
    if not worker_list:
        raise ValueError("at least one worker is required")

    if dry_run:
        return {
            "dry_run": True,
            "goal": goal,
            "workers": [(w.profile, w.title, list(w.skills or [])) for w in worker_list],
            "verifier": verifier,
            "synthesizer": synthesizer,
            "verifier_body_suffix": _VERIFIER_BODY_SUFFIX,
        }

    created = ks.create_swarm(
        conn,
        goal=goal,
        workers=_to_ks_workers(worker_list),
        verifier_assignee=verifier,
        synthesizer_assignee=synthesizer,
        verifier_title="Verify swarm (DCI gate)",
        tenant=tenant,
        created_by=created_by,
        priority=priority,
        idempotency_key=idempotency_key,
    )
    return created.as_dict() | {"verifier_body_suffix": _VERIFIER_BODY_SUFFIX}


# ─── decision layer (DCI gate) ──────────────────────────────────────────

_CONVERGING_KINDS = frozenset({
    CommentKind.SYNTHESIZE.value,
    CommentKind.CONCEDE.value,
})


def decide_gate(
    conn: sqlite3.Connection,
    root_id: str,
    *,
    deadlock_window: int = 3,
) -> GateVerdict:
    """Compute a gate recommendation for the verifier of `root_id`.

    Inputs:
        conn          — sqlite connection with a2a_comment_kinds migrated.
        root_id       — kanban swarm root task id (也是 blackboard 锚点).
        deadlock_window — 死锁检测窗口（默认 3 条同 kind）。

    Decision order (first-match wins):
        1. deadlock  → block, route_hint=regent
        2. majority_for → pass
        3. last entry SYNTHESIZE/CONCEDE → pass (converging)
        4. else → block, route_hint=route_comment(last)
        5. empty thread → block:empty_thread
    """
    thread: Sequence[ThreadEntry] = get_thread(conn, root_id)
    tally = aggregate_votes(thread)

    if not thread:
        return GateVerdict(
            gate="block",
            reason="empty_thread",
            tally=tally,
            deadlocked=False,
            route_hint=None,
        )

    if detect_deadlock(thread, window=deadlock_window):
        return GateVerdict(
            gate="block",
            reason="deadlock:auto_concede_and_synthesize",
            tally=tally,
            deadlocked=True,
            route_hint=deadlock_response(thread),
        )

    if tally.majority == "for":
        return GateVerdict(
            gate="pass",
            reason=f"vote:majority_for ({tally.for_}>{tally.against})",
            tally=tally,
            deadlocked=False,
            route_hint=None,
        )

    last = thread[-1]
    if last.kind in _CONVERGING_KINDS:
        return GateVerdict(
            gate="pass",
            reason=f"converging:{last.kind}",
            tally=tally,
            deadlocked=False,
            route_hint=None,
        )

    hint = route_comment(last)
    return GateVerdict(
        gate="block",
        reason=f"awaiting:{hint.reason}" if hint else "awaiting:unrouted",
        tally=tally,
        deadlocked=False,
        route_hint=hint,
    )
