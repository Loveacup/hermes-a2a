"""DCI-aware orchestrator routing primitives.

Pure functions over `list[ThreadEntry]`. The actual poller / dispatcher
integration lives elsewhere; this module is the decision core so it stays
trivially testable.

Plan: s6m-config/docs/tdd-test-plan.md §3.3.2 / §3.5 (v1.1)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from comment_kind import CommentKind, ThreadEntry


# Routing table: which kind asks which profile to act next.
# CHALLENGE → 太子仲裁; ASK → 翰林院 research; VOTE_* → aggregator; etc.
ROUTE_BY_KIND: dict[str, str] = {
    CommentKind.CHALLENGE.value:        "regent",       # 太子仲裁 (Debate-or-Vote 定向干预)
    CommentKind.ASK.value:              "hanlinyuan",   # 翰林院检索
    CommentKind.EVIDENCE_FOR.value:     "archivist",    # 史馆归档
    CommentKind.EVIDENCE_AGAINST.value: "archivist",
    CommentKind.META_DIRECTIVE.value:   "regent",       # 仅 regent 或 dispatcher 可写，下一步仍归 regent
    CommentKind.VOTE_FOR.value:         "_aggregator",  # 内部聚合，无 profile
    CommentKind.VOTE_AGAINST.value:     "_aggregator",
    CommentKind.ABSTAIN.value:          "_aggregator",
    CommentKind.SYNTHESIZE.value:       "regent",       # 综合权由 regent 拍板
}


@dataclass(frozen=True)
class Routing:
    target_profile: str | None
    reason: str
    is_aggregator: bool = False


def route_comment(entry: ThreadEntry) -> Routing | None:
    """Decide the next assignee for a single comment.

    Returns None when the kind needs no routing action (e.g. PROPOSE on a
    fresh thread — the orchestrator just waits for responses).
    """
    target = ROUTE_BY_KIND.get(entry.kind)
    if target is None:
        return None
    if target == "_aggregator":
        return Routing(target_profile=None, reason=f"vote:{entry.kind}",
                       is_aggregator=True)
    return Routing(target_profile=target, reason=f"kind:{entry.kind}")


# ─── Vote tally ──────────────────────────────────────────────

@dataclass(frozen=True)
class VoteTally:
    for_: int
    against: int
    abstain: int

    @property
    def total(self) -> int:
        return self.for_ + self.against + self.abstain

    @property
    def majority(self) -> str | None:
        """'for' | 'against' | None.

        Abstain never wins. A tie between for and against returns None
        (requires regent intervention).
        """
        if self.for_ > self.against:
            return "for"
        if self.against > self.for_:
            return "against"
        return None


def aggregate_votes(thread: Iterable[ThreadEntry]) -> VoteTally:
    """Count VOTE_FOR / VOTE_AGAINST / ABSTAIN across the thread."""
    f = a = b = 0
    for e in thread:
        if e.kind == CommentKind.VOTE_FOR.value:
            f += 1
        elif e.kind == CommentKind.VOTE_AGAINST.value:
            a += 1
        elif e.kind == CommentKind.ABSTAIN.value:
            b += 1
    return VoteTally(for_=f, against=a, abstain=b)


# ─── Deadlock guard ──────────────────────────────────────────

def detect_deadlock(thread: Sequence[ThreadEntry], window: int = 3) -> bool:
    """Has the conversation stalled in the last `window` comments?

    A deadlock is declared when the most recent `window` comments all share
    the same kind AND none is a converging act (CONCEDE / SYNTHESIZE /
    SUMMARIZE). Empirically matches [[#§8.4 Debate or Vote]]'s "biased
    belief update" trigger condition.
    """
    if window < 2:
        raise ValueError("window must be >= 2")
    if len(thread) < window:
        return False
    tail = thread[-window:]
    kinds = {e.kind for e in tail}
    if len(kinds) > 1:
        return False  # variety means progress
    only_kind = next(iter(kinds))
    converging = {
        CommentKind.CONCEDE.value,
        CommentKind.SYNTHESIZE.value,
        CommentKind.SUMMARIZE.value,
    }
    return only_kind not in converging


def deadlock_response(thread: Sequence[ThreadEntry]) -> Routing | None:
    """When deadlocked, route to regent with a CONCEDE+SYNTHESIZE directive.

    Returns None if not deadlocked.
    """
    if not detect_deadlock(thread):
        return None
    return Routing(
        target_profile="regent",
        reason="deadlock:auto_concede_and_synthesize",
    )


# ─── Convergence guard ───────────────────────────────────────
#
# Plan: prevent 16-profile debates from spinning forever. Three exits:
#   - converged    a converging act landed, OR a side crossed the ratio
#   - force_regent round cap exceeded, OR deadlock detected
#   - continue     keep dispatching the next routing decision
#
# Precedence (highest first):
#   1. latest entry is CONCEDE / SYNTHESIZE / SUMMARIZE   → converged
#   2. consensus_ratio reached among non-ABSTAIN votes    → converged
#   3. detect_deadlock(thread)                            → force_regent
#   4. len(thread) > max_rounds                           → force_regent
#   5. otherwise                                          → continue
#
# Rationale: a converging act is an explicit settlement and trumps every
# other signal. Vote consensus is a soft-but-explicit settlement — even
# if the last three votes share a kind (which detect_deadlock would
# otherwise flag), unanimous voting expresses agreement, not stalemate.
# Deadlock (a stalled non-vote debate) and the round cap are backstops
# that only matter when no settlement has emerged.

_CONVERGING_KINDS = frozenset({
    CommentKind.CONCEDE.value,
    CommentKind.SYNTHESIZE.value,
    CommentKind.SUMMARIZE.value,
})


@dataclass(frozen=True)
class ConvergenceVerdict:
    decision: str            # "continue" | "converged" | "force_regent"
    reason: str              # machine-parsable tag, e.g. "consensus:for"
    round_count: int
    consensus_side: str | None  # "for" | "against" | None
    consensus_ratio: float      # decisive-side / (for + against)


def _consensus(thread: Sequence[ThreadEntry], ratio: float
               ) -> tuple[str | None, float]:
    """Return (winning_side, winning_ratio) — or (None, 0.0) if no winner.

    Denominator excludes ABSTAIN so an abstaining profile doesn't dilute
    the decisive split. Ties never produce a winner.
    """
    tally = aggregate_votes(thread)
    decisive = tally.for_ + tally.against
    if decisive == 0:
        return None, 0.0
    for_ratio = tally.for_ / decisive
    against_ratio = tally.against / decisive
    if for_ratio >= ratio and tally.for_ > tally.against:
        return "for", for_ratio
    if against_ratio >= ratio and tally.against > tally.for_:
        return "against", against_ratio
    return None, max(for_ratio, against_ratio)


def convergence_check(
    thread: Sequence[ThreadEntry],
    max_rounds: int = 5,
    consensus_ratio: float = 0.6,
) -> ConvergenceVerdict:
    """Decide whether a multi-profile debate should continue, settle, or
    escalate to regent arbitration.

    Args:
        thread: ordered list of typed epistemic acts.
        max_rounds: strict upper bound — len(thread) > max_rounds escalates.
        consensus_ratio: fraction of decisive votes required to declare a
            winning side (e.g. 0.6 → 60% of FOR+AGAINST).

    Returns:
        ConvergenceVerdict — pure value; thread is not mutated.
    """
    n = len(thread)

    # 1. converging act wins outright
    if n > 0 and thread[-1].kind in _CONVERGING_KINDS:
        return ConvergenceVerdict(
            decision="converged",
            reason=f"converging_act:{thread[-1].kind}",
            round_count=n,
            consensus_side=None,
            consensus_ratio=0.0,
        )

    # 2. explicit vote consensus (overrides deadlock — unanimous voting is
    #    settlement, not stalemate)
    side, ratio = _consensus(thread, consensus_ratio)
    if side is not None:
        return ConvergenceVerdict(
            decision="converged",
            reason=f"consensus:{side}",
            round_count=n,
            consensus_side=side,
            consensus_ratio=ratio,
        )

    # 3. deadlock — a stalled non-vote debate needs regent now
    if detect_deadlock(thread):
        return ConvergenceVerdict(
            decision="force_regent",
            reason="deadlock:same_kind_window",
            round_count=n,
            consensus_side=None,
            consensus_ratio=ratio,
        )

    # 4. round cap backstop
    if n > max_rounds:
        return ConvergenceVerdict(
            decision="force_regent",
            reason=f"max_rounds:exceeded:{n}>{max_rounds}",
            round_count=n,
            consensus_side=None,
            consensus_ratio=ratio,
        )

    # 5. debate continues
    return ConvergenceVerdict(
        decision="continue",
        reason="rounds_remaining" if n > 0 else "warmup",
        round_count=n,
        consensus_side=None,
        consensus_ratio=ratio,
    )
