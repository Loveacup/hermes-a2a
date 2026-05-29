"""Convergence protocol unit tests for orchestrator_router.

Extends the existing detect_deadlock window scan with a higher-level
convergence_check that decides whether a multi-profile debate should:
  - CONTINUE     ──> still under round cap, consensus not yet emerged
  - CONVERGED    ──> consensus_ratio reached among VOTE_FOR/VOTE_AGAINST,
                     or the most recent act is SYNTHESIZE/CONCEDE/SUMMARIZE
  - FORCE_REGENT ──> round cap exceeded, or deadlock detected
                     ──> regent (太子) must arbitrate

Coverage:
  C-U1 ConvergenceVerdict carries decision + reason + consensus snapshot
  C-U2 Empty / sub-min thread → CONTINUE
  C-U3 max_rounds boundary: round_count == max_rounds → still CONTINUE,
       > max_rounds → FORCE_REGENT
  C-U4 consensus_ratio reached on VOTE_FOR → CONVERGED:for
  C-U5 consensus_ratio reached on VOTE_AGAINST → CONVERGED:against
  C-U6 tied votes never converge (require regent)
  C-U7 ratio uses non-abstain denominator (ABSTAINs ignored when ≥1 side
       crosses the threshold of decisive votes)
  C-U8 latest act CONCEDE / SYNTHESIZE / SUMMARIZE → CONVERGED:converging_act
  C-U9 deadlock (same kind × window) → FORCE_REGENT
  C-U10 custom thresholds override (max_rounds=3, consensus_ratio=0.51)
  C-U11 deadlock takes precedence over round cap, both over consensus
  C-U12 convergence does not mutate the thread (pure function)
"""
from __future__ import annotations

import pytest

from comment_kind import CommentKind, ThreadEntry


def _entry(cid: int, kind: str, *, author: str = "default",
           in_reply_to: int | None = None,
           body: str | None = None) -> ThreadEntry:
    return ThreadEntry(
        comment_id=cid,
        task_id="t-conv",
        author=author,
        body=body or f"<{kind}>",
        kind=kind,
        in_reply_to=in_reply_to,
        metadata={},
        created_at=1_700_000_000 + cid,
        has_a2a_record=True,
    )


# ─────────────────────────────────────────────────────────────
#  C-U1  Verdict shape
# ─────────────────────────────────────────────────────────────
def test_c_u1_verdict_dataclass_shape():
    from orchestrator_router import ConvergenceVerdict

    v = ConvergenceVerdict(
        decision="continue", reason="warmup", round_count=1,
        consensus_side=None, consensus_ratio=0.0,
    )
    assert v.decision == "continue"
    assert v.reason == "warmup"
    assert v.round_count == 1
    assert v.consensus_side is None
    assert v.consensus_ratio == 0.0
    # frozen dataclass — must be hashable / immutable
    with pytest.raises((AttributeError, Exception)):
        v.decision = "converged"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────
#  C-U2  Empty / tiny thread keeps debate alive
# ─────────────────────────────────────────────────────────────
def test_c_u2_empty_thread_continues():
    from orchestrator_router import convergence_check

    v = convergence_check([], max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "continue"
    assert v.round_count == 0


def test_c_u2b_single_propose_continues():
    from orchestrator_router import convergence_check

    thread = [_entry(1, CommentKind.PROPOSE.value, author="regent")]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "continue"


# ─────────────────────────────────────────────────────────────
#  C-U3  Round cap boundary
# ─────────────────────────────────────────────────────────────
def test_c_u3a_round_count_at_cap_still_continues():
    """A 5-round thread without consensus is at the cap, not over it."""
    from orchestrator_router import convergence_check

    # 5 rounds of ASK ping-pong, no votes, no convergence acts
    thread = [
        _entry(1, CommentKind.ASK.value, author="hanlinyuan"),
        _entry(2, CommentKind.REFINE.value, author="default"),
        _entry(3, CommentKind.CLARIFY.value, author="archivist"),
        _entry(4, CommentKind.EVIDENCE_FOR.value, author="archivist"),
        _entry(5, CommentKind.CHALLENGE.value, author="regent"),
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    # At cap (==), not over → continue is acceptable, but boundary
    # semantics is "stop AT cap" by convention. We choose strict >.
    assert v.decision == "continue"
    assert v.round_count == 5


def test_c_u3b_round_count_over_cap_forces_regent():
    """6 non-vote entries with varied kinds → no consensus, no deadlock,
    just over the cap."""
    from orchestrator_router import convergence_check

    varied = [
        CommentKind.ASK.value,
        CommentKind.REFINE.value,
        CommentKind.CLARIFY.value,
        CommentKind.EVIDENCE_FOR.value,
        CommentKind.ASK.value,
        CommentKind.REFINE.value,
    ]
    thread = [_entry(i + 1, k, author=f"p{i}") for i, k in enumerate(varied)]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "force_regent"
    assert "max_rounds" in v.reason
    assert v.round_count == 6


# ─────────────────────────────────────────────────────────────
#  C-U4  Consensus FOR reached
# ─────────────────────────────────────────────────────────────
def test_c_u4_majority_vote_for_converges():
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.VOTE_FOR.value, author="default"),
        _entry(3, CommentKind.VOTE_FOR.value, author="hanlinyuan"),
        _entry(4, CommentKind.VOTE_FOR.value, author="archivist"),
        _entry(5, CommentKind.VOTE_AGAINST.value, author="auditor"),
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "converged"
    assert v.consensus_side == "for"
    assert v.consensus_ratio >= 0.6
    assert "consensus" in v.reason


# ─────────────────────────────────────────────────────────────
#  C-U5  Consensus AGAINST reached
# ─────────────────────────────────────────────────────────────
def test_c_u5_majority_vote_against_converges():
    from orchestrator_router import convergence_check

    thread = [_entry(1, CommentKind.PROPOSE.value, author="regent")]
    thread += [
        _entry(i, CommentKind.VOTE_AGAINST.value, author=f"p{i}")
        for i in range(2, 6)
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "converged"
    assert v.consensus_side == "against"


# ─────────────────────────────────────────────────────────────
#  C-U6  Tied votes never converge
# ─────────────────────────────────────────────────────────────
def test_c_u6_tied_votes_continue():
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.VOTE_FOR.value, author="default"),
        _entry(3, CommentKind.VOTE_AGAINST.value, author="auditor"),
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "continue"
    assert v.consensus_side is None


# ─────────────────────────────────────────────────────────────
#  C-U7  ABSTAINs do not gate consensus
# ─────────────────────────────────────────────────────────────
def test_c_u7_abstains_ignored_in_ratio():
    """4 FOR + 1 AGAINST + 5 ABSTAIN → 4/5 of decisive votes = 0.8 ≥ 0.6."""
    from orchestrator_router import convergence_check

    thread = [_entry(1, CommentKind.PROPOSE.value, author="regent")]
    thread += [_entry(i, CommentKind.VOTE_FOR.value, author=f"f{i}")
               for i in range(2, 6)]
    thread.append(_entry(6, CommentKind.VOTE_AGAINST.value, author="a1"))
    thread += [_entry(i, CommentKind.ABSTAIN.value, author=f"x{i}")
               for i in range(7, 12)]
    v = convergence_check(thread, max_rounds=20, consensus_ratio=0.6)
    assert v.decision == "converged"
    assert v.consensus_side == "for"
    assert v.consensus_ratio == pytest.approx(0.8, abs=0.01)


# ─────────────────────────────────────────────────────────────
#  C-U8  Latest converging act → CONVERGED
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", [
    CommentKind.SYNTHESIZE.value,
    CommentKind.CONCEDE.value,
    CommentKind.SUMMARIZE.value,
])
def test_c_u8_latest_converging_act_terminates(kind):
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.CHALLENGE.value, author="auditor"),
        _entry(3, kind, author="regent"),
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "converged"
    assert v.reason.startswith("converging_act:")
    assert kind in v.reason


# ─────────────────────────────────────────────────────────────
#  C-U9  Deadlock → FORCE_REGENT
# ─────────────────────────────────────────────────────────────
def test_c_u9_deadlock_forces_regent():
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.CHALLENGE.value, author="default"),
        _entry(3, CommentKind.CHALLENGE.value, author="auditor"),
        _entry(4, CommentKind.CHALLENGE.value, author="hanlinyuan"),
    ]
    v = convergence_check(thread, max_rounds=10, consensus_ratio=0.6)
    assert v.decision == "force_regent"
    assert "deadlock" in v.reason


# ─────────────────────────────────────────────────────────────
#  C-U10 Custom thresholds
# ─────────────────────────────────────────────────────────────
def test_c_u10_custom_thresholds_respected():
    """max_rounds=3 honoured on a non-vote thread that has no consensus
    signal to preempt the cap."""
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.REFINE.value, author="default"),
        _entry(3, CommentKind.ASK.value, author="hanlinyuan"),
        _entry(4, CommentKind.CLARIFY.value, author="archivist"),
    ]
    v = convergence_check(thread, max_rounds=3, consensus_ratio=0.51)
    assert v.decision == "force_regent"
    assert "max_rounds" in v.reason


def test_c_u10b_loose_consensus_ratio_converges_earlier():
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.VOTE_FOR.value, author="default"),
        _entry(3, CommentKind.VOTE_AGAINST.value, author="auditor"),
        _entry(4, CommentKind.VOTE_FOR.value, author="archivist"),
    ]
    # 2 FOR / 3 decisive = 0.66 ≥ 0.51 → converged
    v = convergence_check(thread, max_rounds=10, consensus_ratio=0.51)
    assert v.decision == "converged"
    assert v.consensus_side == "for"


# ─────────────────────────────────────────────────────────────
#  C-U11 Precedence: deadlock > round_cap > consensus > continue
# ─────────────────────────────────────────────────────────────
def test_c_u11_deadlock_beats_round_cap_in_reason():
    from orchestrator_router import convergence_check

    # 6 entries — over a cap of 5 — but last 3 are identical CHALLENGEs
    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.REFINE.value, author="default"),
        _entry(3, CommentKind.ASK.value, author="hanlinyuan"),
        _entry(4, CommentKind.CHALLENGE.value, author="auditor"),
        _entry(5, CommentKind.CHALLENGE.value, author="default"),
        _entry(6, CommentKind.CHALLENGE.value, author="regent"),
    ]
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "force_regent"
    assert "deadlock" in v.reason  # deadlock wins the reason field


def test_c_u11b_converging_act_beats_round_cap():
    """If max_rounds is exceeded BUT the latest act is SYNTHESIZE,
    consensus wins — the debate landed before the cap mattered."""
    from orchestrator_router import convergence_check

    thread = [
        _entry(i, CommentKind.REFINE.value, author=f"p{i}")
        for i in range(1, 7)
    ]
    thread.append(_entry(7, CommentKind.SYNTHESIZE.value, author="regent"))
    v = convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert v.decision == "converged"


# ─────────────────────────────────────────────────────────────
#  C-U12 Pure function — does not mutate input
# ─────────────────────────────────────────────────────────────
def test_c_u12_does_not_mutate_thread():
    from orchestrator_router import convergence_check

    thread = [
        _entry(1, CommentKind.PROPOSE.value, author="regent"),
        _entry(2, CommentKind.VOTE_FOR.value, author="default"),
        _entry(3, CommentKind.VOTE_FOR.value, author="auditor"),
    ]
    snapshot = list(thread)
    convergence_check(thread, max_rounds=5, consensus_ratio=0.6)
    assert thread == snapshot
