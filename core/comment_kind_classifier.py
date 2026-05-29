"""Classify free-text task_comments into DCI kinds.

Bridges the gap identified during DCI pipeline E2E design:
  - Agents write comments via `hermes kanban comment` / kanban_comment tool
    which only touches task_comments, NOT the bypass table.
  - For the orchestrator to route on DCI kinds, comments must be classified
    and recorded into a2a_comment_kinds.

Two-stage classifier:
  1. Explicit prefix — body starts with `[CHALLENGE]`, `[propose]`, etc.
     (case-insensitive). Highest signal; never overridden.
  2. Keyword heuristic — Chinese + English phrase patterns map to a kind.
     Used when no prefix is present.

Returns None when nothing matches; callers should NOT default to PROPOSE
silently (let the view's COALESCE handle the legacy default).

Plan: not in tdd-test-plan.md v1.1 — this is the post-RED gap identified
during DCI pipeline E2E design. Documented for v1.2 inclusion.
"""
from __future__ import annotations

import re

from comment_kind import CommentKind

# Prefix syntax: "[CHALLENGE]" / "[challenge]" / "【CHALLENGE】"
# at the start of body (whitespace tolerant)
_PREFIX_RE = re.compile(
    r"^\s*[\[【]\s*([a-z_]+)\s*[\]】]\s*",
    re.IGNORECASE,
)

# Map phrase fragments → kind. Order matters: more specific first.
# Keep this list conservative — false positives are worse than misses.
_PATTERNS: list[tuple[re.Pattern[str], CommentKind]] = [
    # CHALLENGE — Chinese 质疑/挑战/反对/不同意 + English challenge/disagree
    (re.compile(r"(质疑|挑战|反对|不同意|存疑|我不认为|这忽略了)", re.I),
     CommentKind.CHALLENGE),
    (re.compile(r"\b(challenge|disagree|i\s+disagree|i\s+question)\b", re.I),
     CommentKind.CHALLENGE),

    # CONCEDE — 同意/认可/我接受/确实 + concede/agree/accept
    (re.compile(r"(让步|认可|接受你的|确实如此|你说的对|同意你的观点)", re.I),
     CommentKind.CONCEDE),
    (re.compile(r"\b(concede|i\s+agree|you'?re\s+right|fair\s+point)\b", re.I),
     CommentKind.CONCEDE),

    # EVIDENCE_AGAINST — has source + against framing
    (re.compile(r"(反例|反证|根据.*显示.*相反|相反的证据)", re.I),
     CommentKind.EVIDENCE_AGAINST),
    (re.compile(r"\b(counter[- ]evidence|evidence\s+against)\b", re.I),
     CommentKind.EVIDENCE_AGAINST),

    # EVIDENCE_FOR — citation/data/source 表达
    (re.compile(r"(根据.*论文|引用|数据显示|证据表明|来源[:：])", re.I),
     CommentKind.EVIDENCE_FOR),
    (re.compile(r"\b(per\s+\w+|source:|according\s+to|paper\s+\d+\.\d+|arxiv)\b", re.I),
     CommentKind.EVIDENCE_FOR),

    # SYNTHESIZE — 综合/总结/我的判断/结论
    (re.compile(r"(综合.*意见|综合各方|我的判断|结论是|总的来看)", re.I),
     CommentKind.SYNTHESIZE),
    (re.compile(r"\b(synthesi[sz]e|to\s+synthesi[sz]e|in\s+summary|the\s+verdict)\b", re.I),
     CommentKind.SYNTHESIZE),

    # SUMMARIZE — pure recap, no new claim
    (re.compile(r"(回顾.*讨论|目前为止|让我总结)", re.I),
     CommentKind.SUMMARIZE),
    (re.compile(r"\b(summari[sz]e|recap|so\s+far)\b", re.I),
     CommentKind.SUMMARIZE),

    # ASK — question marks + asking words
    (re.compile(r"(请问|是否|能否|可以.*吗|为什么.*\?|\?$)"),
     CommentKind.ASK),
    (re.compile(r"\b(could\s+you|can\s+you|what\s+if|why\s+would|\?$)", re.I),
     CommentKind.ASK),

    # REFINE — 建议改成/不如/可以优化
    (re.compile(r"(不如|建议改|更好的做法|可以优化|换一种)", re.I),
     CommentKind.REFINE),
    (re.compile(r"\b(refine|i\s+suggest|how\s+about|alternative)\b", re.I),
     CommentKind.REFINE),

    # CLARIFY — 澄清/解释/我的意思
    (re.compile(r"(澄清|让我解释|我的意思是|换句话说)", re.I),
     CommentKind.CLARIFY),
    (re.compile(r"\b(to\s+clarify|i\s+meant|in\s+other\s+words)\b", re.I),
     CommentKind.CLARIFY),

    # VOTE buckets
    (re.compile(r"(我投赞成|赞成票|👍)", re.I),
     CommentKind.VOTE_FOR),
    (re.compile(r"\bvote[: ]+for\b", re.I),
     CommentKind.VOTE_FOR),
    (re.compile(r"(我投反对|反对票|👎)", re.I),
     CommentKind.VOTE_AGAINST),
    (re.compile(r"\bvote[: ]+against\b", re.I),
     CommentKind.VOTE_AGAINST),
    (re.compile(r"(弃权|我不参与)", re.I),
     CommentKind.ABSTAIN),
    (re.compile(r"\babstain\b", re.I),
     CommentKind.ABSTAIN),

    # PROPOSE — opening claims, 提议/我认为/let me propose
    (re.compile(r"(提议|建议|我认为|主张|我提出)", re.I),
     CommentKind.PROPOSE),
    (re.compile(r"\b(propose|i\s+suggest\s+we|let\s+me\s+propose|i\s+think\s+we\s+should)\b", re.I),
     CommentKind.PROPOSE),
]


def classify_from_prefix(body: str) -> CommentKind | None:
    """Detect an explicit `[KIND]` prefix. Returns the kind or None."""
    if not body:
        return None
    m = _PREFIX_RE.match(body)
    if not m:
        return None
    raw = m.group(1).lower()
    try:
        return CommentKind(raw)
    except ValueError:
        return None


def classify_heuristic(body: str) -> CommentKind | None:
    """Phrase-pattern fallback. Returns None when nothing matches."""
    if not body:
        return None
    for pat, kind in _PATTERNS:
        if pat.search(body):
            return kind
    return None


def classify(body: str) -> CommentKind | None:
    """Two-stage classify. Prefix wins over heuristic."""
    by_prefix = classify_from_prefix(body)
    if by_prefix is not None:
        return by_prefix
    return classify_heuristic(body)


def strip_prefix(body: str) -> str:
    """Remove a leading `[KIND]` prefix for storage hygiene (optional)."""
    if not body:
        return body
    return _PREFIX_RE.sub("", body, count=1).lstrip()
