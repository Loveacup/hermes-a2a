"""P1-C: expand classifier patterns to lift coverage from 34% → 70%+.

Production data (224 historical task_comments) breaks down as:
  - 76 / 224 (34%) matched the original PATTERNS (PROPOSE / EVIDENCE_FOR / ASK)
  - 148 / 224 (66%) fell to the PROPOSE default — they are mostly:
      * Regent operational directives (【父皇批示】 / 【监国处置】)  → META_DIRECTIVE
      * Audit / review outputs (稽核详情 / 审计 / 复审)                → EVIDENCE_FOR
      * Handoff / delivery reports (交付 / handoff / 完成)             → SUMMARIZE
      * Revision / fix reports (修订完成 / 修复完成)                    → REFINE
      * Block reports (BLOCKED / 阻断)                                   → CHALLENGE

This test fixes the *shapes* the classifier must recognise. Each row is a
real-world body excerpt + the expected kind. If the count of expected matches
falls below the 70% target, the test fails and forces a pattern update.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "core"))

from comment_kind import CommentKind  # noqa: E402
from comment_kind_classifier import classify  # noqa: E402


# 30 real-shape excerpts drawn from prod kanban.db default rows.
# Format: (excerpt, expected_kind, reason)
PROD_SHAPES: list[tuple[str, CommentKind, str]] = [
    # ── META_DIRECTIVE: Regent operational directives ───────────────────
    ("【父皇批示】三项决策已准：(1)职责措辞...",
     CommentKind.META_DIRECTIVE, "regent directive marker"),
    ("【父皇纠偏】本链定性错误，本链停止...",
     CommentKind.META_DIRECTIVE, "regent correction marker"),
    ("【监国诊断】planner 2x crash 根因...",
     CommentKind.META_DIRECTIVE, "regent diagnosis marker"),
    ("【监国处置】原审阻断已由 t_cf116fb5 修复，归档以免继续占用活跃板",
     CommentKind.META_DIRECTIVE, "regent ops marker"),
    ("【修正】(1)skill路径已标注本地完整路径...",
     CommentKind.META_DIRECTIVE, "regent revision marker"),

    # ── EVIDENCE_FOR: audit/review verdicts + concrete artifacts ────────
    ("## 御史台稽核详细记录 t_12343eb9 ### 稽核对象 ...",
     CommentKind.EVIDENCE_FOR, "audit report"),
    ("## 御史台稽核详情 ### 产出物位置 所有文件在 ...",
     CommentKind.EVIDENCE_FOR, "audit detail"),
    ("审计发现已记录，等待将作监复稽。",
     CommentKind.EVIDENCE_FOR, "audit finding"),
    ("## 门下省复审详细发现 ### 校验证据 C1 — skill 同步验证...",
     CommentKind.EVIDENCE_FOR, "review with evidence"),
    ("## 司验院 E2E 验证报告 测试概览 - 冒烟测试：3/3 PASS",
     CommentKind.EVIDENCE_FOR, "verification report"),
    ("## 测试报告已产出 报告路径...",
     CommentKind.EVIDENCE_FOR, "test report"),
    ("## 稽核报告 总体评定：FAIL — 阻断",
     CommentKind.EVIDENCE_FOR, "audit verdict"),

    # ── SUMMARIZE: handoff / delivery / completion reports ──────────────
    ("review-required handoff: 变更：15 profile 权限矩阵补 show/list/link 只读权限",
     CommentKind.SUMMARIZE, "handoff report"),
    ("## 交付物清单 文件位置: /Users/alexcai/.hermes/kanban/...",
     CommentKind.SUMMARIZE, "delivery manifest"),
    ("## 执行总结 ### 变更清单 1. registry/config.yaml...",
     CommentKind.SUMMARIZE, "execution summary"),
    ("## inbox-batch4 处理完成 ✓ ### 19/19 笔记全部处理",
     CommentKind.SUMMARIZE, "completion recap"),
    ("## 注入完成 — 验收核对 变更文件: ...",
     CommentKind.SUMMARIZE, "injection done"),
    ("## 演习完成 — drill-2026Q2-001 结果：通过",
     CommentKind.SUMMARIZE, "drill completion"),
    ("## 归档执行摘要 ### 修改的文件 ...",
     CommentKind.SUMMARIZE, "archive summary"),
    ("## P0-2 调研完成 — 统一高危工具 Gate Policy 设计方案",
     CommentKind.SUMMARIZE, "research completion"),
    ("## Delivery Bridge — 工部交付 ### Changed files - /Users/alexcai/.hermes/...",
     CommentKind.SUMMARIZE, "delivery handoff (en)"),
    ("## Safety cleanup handoff Changed file: ...",
     CommentKind.SUMMARIZE, "safety handoff"),

    # ── REFINE: revision/fix reports ────────────────────────────────────
    ("修订完成，三项封驳意见逐一回应：1. 拓扑矩阵矛盾 - 4.2 横向通信规则已重写",
     CommentKind.REFINE, "revision response"),
    ("## 修复摘要 ### 修复的 3 类问题 Fix A — HERMES_PROFILE 环境变量...",
     CommentKind.REFINE, "fix report"),
    ("## 将作监大匠修复报告 ### 修复1：A 路 #15 央视链接...",
     CommentKind.REFINE, "fix detail report"),

    # ── CHALLENGE: block/halt actions ───────────────────────────────────
    ("BLOCKED: 验证完成，主动终止",
     CommentKind.CHALLENGE, "block marker"),

    # ── Preserved originals (sanity — must still match) ─────────────────
    ("我质疑这个方案有重大风险",
     CommentKind.CHALLENGE, "challenge keyword"),
    ("根据 paper 2.3 数据显示效果显著",
     CommentKind.EVIDENCE_FOR, "evidence citation"),
    ("[PROPOSE] 我提议使用方案 B",
     CommentKind.PROPOSE, "explicit prefix"),
    ("是否可以考虑回滚？",
     CommentKind.ASK, "question form"),
]


def test_classifier_meets_70pct_on_prod_shapes():
    """Aggregate accuracy on production-shaped excerpts must exceed 70%."""
    hits = 0
    misses: list[tuple[str, CommentKind, CommentKind | None]] = []
    for body, want, _reason in PROD_SHAPES:
        got = classify(body)
        if got == want:
            hits += 1
        else:
            misses.append((body[:50], want, got))
    rate = hits / len(PROD_SHAPES)
    if rate < 0.70:
        for b, want, got in misses[:10]:
            print(f"  miss: want={want.value:18s} got={(got.value if got else 'None'):18s} | {b}")
    assert rate >= 0.70, (
        f"classifier hit-rate {rate:.1%} below 70% target on {len(PROD_SHAPES)} prod shapes"
    )


@pytest.mark.parametrize("body,want,_reason", PROD_SHAPES, ids=[s[2] for s in PROD_SHAPES])
def test_classifier_individual_shape(body: str, want: CommentKind, _reason: str):
    """Per-shape diagnostic — each excerpt should map to its expected kind."""
    got = classify(body)
    assert got == want, f"want={want.value} got={got.value if got else 'None'} | {body[:60]}"
