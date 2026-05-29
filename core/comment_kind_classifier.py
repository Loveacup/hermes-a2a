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
# Conservative core patterns + P1-C production-shape extensions calibrated
# against 224 historical task_comments (target ≥70% coverage).
#
# Ordering rationale (highest signal first — once a pattern matches, the
# remainder are not checked):
#   1. META_DIRECTIVE — 太子/监国 markers are unambiguous
#   2. EVIDENCE_FOR (audit reports) — compound noun phrases (稽核报告, 测试报告)
#      win over single-keyword dissent words inside their content.
#   3. SUMMARIZE (handoff / completion) — work recap framings beat keywords
#      embedded in summary bodies.
#   4. REFINE (修订完成 / 修复报告) — revision verbs beat 封驳/纠正 used as
#      references to the prior dispute being addressed.
#   5. CHALLENGE / CONCEDE / EVIDENCE_AGAINST / EVIDENCE_FOR (citation) /
#      SYNTHESIZE / SUMMARIZE (generic) — broader phrases.
#   6. ASK / CLARIFY
#   7. VOTE buckets
#   8. PROPOSE — opening claims (catch-all for declarative sentences).
_PATTERNS: list[tuple[re.Pattern[str], CommentKind]] = [
    # ── 1. META_DIRECTIVE: regent (太子) operational directives ────────
    # 【父皇XX】/【监国XX】 / 【修正】markers are exclusive to regent ops.
    (re.compile(r"【\s*(父皇|监国|太子|圣谕|敕令|修正)[^】]*】", re.I),
     CommentKind.META_DIRECTIVE),
    (re.compile(r"(批示|纠偏|敕令|圣谕|监国处置|监国诊断)", re.I),
     CommentKind.META_DIRECTIVE),

    # ── 2. EVIDENCE_FOR (high-confidence compound patterns) ────────────
    # Audit / review outputs cite findings + concrete artifacts; map to
    # EVIDENCE_FOR ahead of CHALLENGE so 阻断/FAIL inside an audit body
    # does not mis-route the entire report.
    (re.compile(r"(稽核详[情记细]|稽核报告|稽核(执行)?详细记录"
                r"|审计详[情记细]|审计报告|审计发现|审计记录"
                r"|复稽报告?|复稽详[情记]|复审详[情记]|复审结果|复审报告"
                r"|审查结果|审查详[情记]|校验证据|稽核记录)", re.I),
     CommentKind.EVIDENCE_FOR),
    (re.compile(r"(测试报告|验证报告|E2E.*报告|总体[评结]定|测试结果|验证结果"
                r"|\d+\s*/\s*\d+\s*PASS|总体结果|检验结果)", re.I),
     CommentKind.EVIDENCE_FOR),

    # ── 3. SUMMARIZE (high-confidence handoff / delivery / completion) ─
    (re.compile(r"(review[- ]required\s+handoff|交付物清单|执行总结|执行摘要"
                r"|交付清单|交付摘要|归档(执行)?摘要|工部交付"
                r"|交付桥|delivery\s+bridge)", re.I),
     CommentKind.SUMMARIZE),
    (re.compile(r"(处理完成|注入完成|演习完成|归档完成|调研完成|执行完成"
                r"|批次.*处理|inbox.*处理|检索完成)", re.I),
     CommentKind.SUMMARIZE),
    (re.compile(r"\b(handoff|changed\s+files?|delivery\s+bridge)\b", re.I),
     CommentKind.SUMMARIZE),

    # ── 4. REFINE (high-confidence revision / fix reports) ─────────────
    (re.compile(r"(修订完成|修订报告|修复报告|修复摘要|修复完成"
                r"|大匠修复|逐一回应)", re.I),
     CommentKind.REFINE),

    # ── 5. CHALLENGE: block / halt / dissent (after audit reports) ─────
    (re.compile(r"^\s*BLOCKED\b", re.I),
     CommentKind.CHALLENGE),
    (re.compile(r"(阻断|拒绝合入|否决|不予通过|不批|驳回|封驳)", re.I),
     CommentKind.CHALLENGE),
    (re.compile(r"(质疑|挑战|反对|不同意|存疑|我不认为|这忽略了)", re.I),
     CommentKind.CHALLENGE),
    (re.compile(r"\b(challenge|disagree|i\s+disagree|i\s+question|blocked\b)\b", re.I),
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
