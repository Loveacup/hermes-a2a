#!/usr/bin/env python3
"""
A2A 自动讨论分类引擎 — 内阁群消息智能分析，决定是否触发讨论、讨论模式及深度。

本模块分析 cabinet group 中的文本消息，通过正则模式匹配和加权评分机制，
自动判定：(1) 是否应触发讨论，(2) 采用 ROLEPLAY 还是 SYNTHESIZE 模式，
(3) 辩论轮数或分析深度。

设计原则：
  - 双轨评分：每条消息同时计算 roleplay_score 和 synthesize_score
  - 阈值过滤：总分 < 3 则不触发（NO_DISCUSS）
  - 模式比较：得分高者胜出；持平则倾向 SYNTHESIZE（深度优先）
  - 中文优先：所有正则模式均为中文字符串，精确匹配中文语境

用法示例:
    from auto_discuss import classify_message, AutoDiscussDecision

    decision = classify_message("三省六部架构是不是该重构了？")
    if decision.should_discuss:
        print(f"模式: {decision.mode}, 轮数: {decision.rounds}")
    else:
        print("不触发讨论")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar, Pattern


# ═══════════════════════════════════════════════════════════════════════════════
# 数据类 — 分类决策结果
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AutoDiscussDecision:
    """
    自动讨论分类决策结果。

    Attributes:
        should_discuss: 是否应触发讨论（True / False）
        mode: 讨论模式 — "roleplay"（双边辩论）、"synthesize"（综合研判）或 ""（不触发）
        topic: 提取/精炼后的讨论议题
        rounds: 辩论轮数（仅 roleplay 模式有效，默认 3）
        depth: 分析深度 — "shallow" / "normal" / "deep"（仅 synthesize 模式有效）
        regent_persona: 太子（regent）的角色描述/立场
        initial_stance: 小黄（default）的初始立场
        reasoning: 分类决策的推理说明（为什么做出此判断）
    """
    should_discuss: bool = False
    mode: str = ""  # "roleplay" | "synthesize" | ""
    topic: str = ""
    rounds: int = 3        # roleplay 默认 3 轮
    depth: str = "deep"    # synthesize 默认深度
    regent_persona: str = ""
    initial_stance: str = ""
    reasoning: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# 正则模式库 — 中文触发词与过滤词
# ═══════════════════════════════════════════════════════════════════════════════

# ── ROLEPLAY 触发模式（双边辩论）─────────────────────────────────────────────
# 分组权重：匹配到的模式按其所在分组获得不同分数加成
#   - 对立类（组 1）："vs"、"对比"、"选哪个" → 强辩论信号，+4 分
#   - 应否类（组 2）："该不该"、"要不要"、"是否应当" → 明确二元选择，+4 分
#   - 利弊类（组 3）："利弊"、"优劣"、"得失" → 权衡辩论，+3 分
#   - 变更类（组 4）："重构"、"重写"、"替换"、"迁移"、"升级" → 技术变更提议，+3 分
#   - 方案对比（组 5）："你觉得.*还是"、"方案.*对比"、"A.*B.*哪个" → 多选项辩论，+3 分

_ROLEPLAY_PATTERNS: dict[str, list[str]] = {
    # 对立/二选一类 —— 强辩论信号
    "opposition": [
        r"\bvs\b",                # "A vs B"
        r"对比",                   # "方案对比"
        r"选哪个",                 # "A 和 B 选哪个"
        r"二选一",
        r"哪个更[好好优强]",       # "哪个更好"
    ],
    # 应否/该不该类 —— 二元价值判断
    "ought_to": [
        r"该不该",                 # "该不该做 X"
        r"要不要",                 # "要不要启动"
        r"是否应当",
        r"是否该",
        r"应不应[该当]",
        r"值不值得",
    ],
    # 利弊权衡类 —— 多因素辩论
    "tradeoff": [
        r"利弊",                   # "权衡利弊"
        r"优劣",                   # "分析优劣"
        r"得失",
        r"权衡",
        r"取舍",
    ],
    # 技术变更提议类 —— 架构/实现层面决策
    "change_proposal": [
        r"重构",                   # "重构 auth 模块"
        r"重写",                   # "重写 middleware"
        r"替换",                   # "替换底层库"
        r"迁移",                   # "从 X 迁移到 Y"
        r"升级",                   # "升级到 v2"
        r"废除",                   # "废除旧接口"
    ],
    # 多方案对比类 —— 选项间辩论
    "alternatives": [
        r"你觉得.*还是",           # "你觉得 A 好还是 B 好"
        r"方案.*对比",
        r"对比.*方案",
        r"[ABC].*[ABC].*哪个",     # "A 和 B 哪个"
        r".{1,20}还是.{1,20}",    # "X 还是 Y" — 简单二选一（"React 还是 Vue"）
    ],
}

# ── SYNTHESIZE 触发模式（综合研判）──────────────────────────────────────────
# 分组权重：
#   - 分析类（组 1）："分析"、"评估"、"审计"、"审查"、"调研"、"盘点" → 深度分析，+4 分
#   - 深层问题（组 2）："为什么"、"怎么办"、"如何"、"怎么" → 探究原因/方法，+3 分
#     注意：必须排除简单操作问法（"怎么配置"、"怎么用" 等，见过滤模式）
#   - 产出类（组 3）："报告"、"方案"、"建议"、"规划"、"路线图" → 要求产出物，+4 分

_SYNTHESIZE_PATTERNS: dict[str, list[str]] = {
    # 分析/评估类 —— 深度研判信号
    "analysis": [
        r"分析",                   # "帮我分析"
        r"评估",                   # "风险评估"
        r"审计",                   # "安全审计"
        r"审查",                   # "代码审查"
        r"调研",                   # "市场调研"
        r"盘点",                   # "技术盘点"
        r"研判",                   # "综合研判"
        r"诊断",                   # "性能诊断"
    ],
    # 深层问题类 —— 探究原因/方法
    "deep_question": [
        r"为什么",                 # "为什么延迟高"
        r"怎么办",                 # "遇到瓶颈怎么办"
        r"如何(?!配置|使用|操作|启动|安装|部署)",  # "如何设计" 但排除 "如何配置"
        r"怎么(?!配置|用|操作|启动|安装|部署)",    # "怎么优化" 但排除 "怎么配置"
        r"原因.*是什么",
        r"根源",
        r"本质",
    ],
    # 产出物请求类 —— 要求结构化报告
    "output_request": [
        r"报告",                   # "出个报告"
        r"方案",                   # "设计一个方案"
        r"建议",                   # "给点建议"
        r"规划",                   # "技术规划"
        r"路线图",                 # "产品路线图"
        r"举措",                   # "应对举措"
        r"对策",                   # "安全对策"
    ],
}

# ── NO_DISCUSS 过滤模式（不触发讨论）────────────────────────────────────────
# 匹配到以下任一模式的消息将直接跳过，不做讨论触发。
# 这些模式覆盖：简单问答、问候语、单步指令、已指定模式的指令。

_NO_DISCUSS_PATTERNS: list[str] = [
    # 简单操作问答 —— 无需辩论或深度分析
    r"是什么",                    # "X 是什么"
    r"怎么配置",
    r"怎么用",
    r"在哪[里呢]?",               # "在哪"
    r"在哪里",
    r"是什么意[思思]",
    r"怎么[搞弄做]",               # "怎么搞"
    r"多少钱",
    r"几点",
    r"什么时[间候]",

    # 问候/闲聊 —— 非任务性消息
    r"^(早|好|嗯|哦|行|成|OK|欧[克了]|ok)\s*$",
    r"^(hi|hello|hey)\b",         # 英文问候
    r"你好",
    r"在吗",
    r"在不",
    r"晚安",
    r"早上好",
    r"晚上好",

    # 单步查询指令 —— 无需讨论
    r"查一下",
    r"帮我看看",
    r"显示",
    r"列出",
    r"搜索",
    r"查找",
    r"打开",
    r"帮我看",

    # 已指定模式的指令 —— 调用方已明确模式，不再自动分类
    r"^(讨论模式|朝议|内阁)",       # "讨论模式：..."、"朝议 ..."、"内阁..."
    r"^ROLEPLAY",
    r"^SYNTHESIZE",
    r"^@.*\b(roleplay|synthesize)",  # "@xxx roleplay ..."
]

# ── 简单操作排除列表 ────────────────────────────────────────────────────────
# 当消息中包含深层问题词（"为什么"、"怎么"、"如何"）时，需要检查是否同时
# 命中了简单操作词。若命中简单操作词，则该深层问题不计分。
_SIMPLE_ACTION_PATTERNS: list[str] = [
    r"配置",
    r"使用",
    r"操作",
    r"启动",
    r"安装",
    r"部署",
    r"登录",
    r"注册",
    r"下载",
    r"打开",
]


def _compile_patterns(pattern_dict: dict[str, list[str]]) -> dict[str, list[Pattern[str]]]:
    """
    将字符串模式字典编译为编译后的正则对象字典。

    Args:
        pattern_dict: 键为分组名，值为正则字符串列表的字典。

    Returns:
        键为分组名，值为 re.Pattern 对象列表的字典。
    """
    return {
        group: [re.compile(p, re.IGNORECASE) for p in patterns]
        for group, patterns in pattern_dict.items()
    }


def _compile_list(patterns: list[str]) -> list[Pattern[str]]:
    """
    将字符串模式列表编译为编译后的正则对象列表。

    Args:
        patterns: 正则字符串列表。

    Returns:
        re.Pattern 对象列表。
    """
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# ── 预编译所有正则（模块加载时一次性编译，避免重复）
_ROLEPLAY_RE: dict[str, list[Pattern[str]]] = _compile_patterns(_ROLEPLAY_PATTERNS)
_SYNTHESIZE_RE: dict[str, list[Pattern[str]]] = _compile_patterns(_SYNTHESIZE_PATTERNS)
_NO_DISCUSS_RE: list[Pattern[str]] = _compile_list(_NO_DISCUSS_PATTERNS)
_SIMPLE_ACTION_RE: list[Pattern[str]] = _compile_list(_SIMPLE_ACTION_PATTERNS)


# ═══════════════════════════════════════════════════════════════════════════════
# 主题提取
# ═══════════════════════════════════════════════════════════════════════════════


def extract_topic(message: str) -> str:
    """
    从原始消息中提取/精炼讨论议题。

    处理步骤：
      1. 去除 @mention（如 @小黄_主频道、@someone）
      2. 去除首尾空白与标点噪声
      3. 截断过长的议题（> 80 字符则取前 80 字符 + "…"）

    Args:
        message: 原始消息文本。

    Returns:
        精炼后的议题字符串。

    Examples:
        >>> extract_topic("@小黄_主频道 三省六部架构该不该重构？")
        '三省六部架构该不该重构？'
        >>> extract_topic("  讨论一下 auth 模块的重构方案  ")
        '讨论一下 auth 模块的重构方案'
    """
    # 1. 去除 @mention（匹配 @用户名 形式，用户名可含中文/字母/数字/下划线）
    cleaned = re.sub(r"@[\w\u4e00-\u9fff]+", "", message)

    # 2. 去除首尾空白及常见噪音字符
    cleaned = cleaned.strip().strip("，。！？；：、\"\"''「」『』 ()（）[]【】")

    # 3. 二次空白压缩（多个空白字符合并为一个空格）
    cleaned = re.sub(r"\s+", " ", cleaned)

    # 4. 长度截断：议题不宜过长
    if len(cleaned) > 80:
        cleaned = cleaned[:80] + "…"

    return cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# 评分函数
# ═══════════════════════════════════════════════════════════════════════════════


def _score_roleplay(message: str) -> int:
    """
    计算消息的 ROLEPLAY（双边辩论）模式得分。

    评分规则（满分 10）：
      - opposition（对立/二选一）：每个匹配 +4，最多 +4
      - ought_to（应否判断）：每个匹配 +4，最多 +4
      - tradeoff（利弊权衡）：每个匹配 +3，最多 +3
      - change_proposal（技术变更）：每个匹配 +3，最多 +3
      - alternatives（方案对比）：每个匹配 +3，最多 +3
      - 上限截断为 10

    Args:
        message: 消息文本。

    Returns:
        ROLEPLAY 模式评分（0-10 的整数）。
    """
    score = 0

    # 分组权重配置
    group_weights: dict[str, int] = {
        "opposition": 4,
        "ought_to": 4,
        "tradeoff": 3,
        "change_proposal": 3,
        "alternatives": 3,
    }
    # 每组加分上限
    group_max: dict[str, int] = {
        "opposition": 4,
        "ought_to": 4,
        "tradeoff": 3,
        "change_proposal": 3,
        "alternatives": 3,
    }

    for group, patterns in _ROLEPLAY_RE.items():
        group_score = 0
        weight = group_weights.get(group, 3)
        for pat in patterns:
            if pat.search(message):
                group_score += weight
        # 应用该组加分上限
        score += min(group_score, group_max.get(group, 3))

    return min(score, 10)


def _score_synthesize(message: str) -> int:
    """
    计算消息的 SYNTHESIZE（综合研判）模式得分。

    评分规则（满分 10）：
      - analysis（分析/评估/审计）：每个匹配 +4，最多 +4
      - deep_question（深层问题）：每个匹配 +3，最多 +4
        注意：如果消息中同时包含简单操作词（"配置"、"使用" 等），
        则该深层问题不计分。
      - output_request（报告/方案/建议）：每个匹配 +4，最多 +4
      - 上限截断为 10

    Args:
        message: 消息文本。

    Returns:
        SYNTHESIZE 模式评分（0-10 的整数）。
    """
    score = 0

    # 分组权重配置
    group_weights: dict[str, int] = {
        "analysis": 4,
        "deep_question": 3,
        "output_request": 4,
    }
    group_max: dict[str, int] = {
        "analysis": 4,
        "deep_question": 4,
        "output_request": 4,
    }

    # 检查消息是否包含简单操作词（用于过滤深层问题）
    has_simple_action = any(
        pat.search(message) for pat in _SIMPLE_ACTION_RE
    )

    for group, patterns in _SYNTHESIZE_RE.items():
        group_score = 0
        weight = group_weights.get(group, 3)
        for pat in patterns:
            if pat.search(message):
                # 深层问题特殊处理：若消息含简单操作词，则不计分
                if group == "deep_question" and has_simple_action:
                    continue
                group_score += weight
        score += min(group_score, group_max.get(group, 3))

    return min(score, 10)


# ═══════════════════════════════════════════════════════════════════════════════
# 轮数与深度判定
# ═══════════════════════════════════════════════════════════════════════════════


def _determine_rounds(message: str, roleplay_score: int) -> int:
    """
    根据消息内容和 score 确定 ROLEPLAY 辩论轮数。

    规则：
      - 2 轮：二元选择（"选 A 还是 B"、简单 "vs"、明确二选一）
      - 3 轮：标准可辩论议题（DEFAULT）
      - 4 轮：复杂多因素议题（含"架构重构"、"技术选型"等关键词）
      - 5 轮：关键决策（含"战略方向"、"重大变更"等关键词）

    Args:
        message: 消息文本。
        roleplay_score: ROLEPLAY 评分（用于辅助判断复杂度）。

    Returns:
        建议辩论轮数（2-5 的整数）。
    """
    # 二选一 / 简单 vs → 2 轮即可
    binary_patterns = [
        re.compile(r"选.*还是"),
        re.compile(r"还是.*[\?？]"),        # "X 还是 Y？" — 二选一问句
        re.compile(r"\bvs\b"),
        re.compile(r"二选一"),
        re.compile(r"[ABC].*[ABC].*哪个"),
        re.compile(r"该不该"),
        re.compile(r"要不要"),
    ]
    if any(p.search(message) for p in binary_patterns):
        return 2

    # 战略级 / 重大变更 → 5 轮
    critical_patterns = [
        re.compile(r"战略[方方]向"),
        re.compile(r"重大变[更改]"),
        re.compile(r"全局.*重构"),
        re.compile(r"核心架构"),
        re.compile(r"根本[性性].*改变"),
    ]
    if any(p.search(message) for p in critical_patterns):
        return 5

    # 复杂多因素 → 4 轮
    complex_patterns = [
        re.compile(r"架构重构"),
        re.compile(r"技术选型"),
        re.compile(r"多方案"),
        re.compile(r"全面.*改革"),
        re.compile(r"系统.*升级"),
    ]
    if any(p.search(message) for p in complex_patterns):
        return 4

    # 高分但非特殊 → 仍用 4 轮
    if roleplay_score >= 7:
        return 4

    # 默认 3 轮
    return 3


def _determine_depth(message: str, synthesize_score: int) -> str:
    """
    根据消息内容和 score 确定 SYNTHESIZE 分析深度。

    规则：
      - shallow（初步研判）：聚焦型问题，快速分析（如单一"分析"词 + 低分）
      - normal（标准深度）：常规多角度分析（中等得分或标准深度问题）
      - deep（深度研判）：需出具综合报告（DEFAULT，高分或含"报告"、"方案"等产出词）

    Args:
        message: 消息文本。
        synthesize_score: SYNTHESIZE 评分。

    Returns:
        分析深度："shallow" / "normal" / "deep"。
    """
    # 浅度：聚焦型问题、快速分析
    shallow_patterns = [
        re.compile(r"(简单|快速|粗略).*(分析|评估|看看)"),
        re.compile(r"(分析|评估).*(一下|一下下|看看)"),
        re.compile(r"初步"),
    ]
    if any(p.search(message) for p in shallow_patterns):
        return "shallow"

    # 深度：含产出物请求词 或 高分
    deep_output_patterns = [
        re.compile(r"报告"),
        re.compile(r"方案"),
        re.compile(r"规划"),
        re.compile(r"路线图"),
        re.compile(r"深度"),
        re.compile(r"全面"),
        re.compile(r"详尽"),
    ]
    if any(p.search(message) for p in deep_output_patterns) or synthesize_score >= 7:
        return "deep"

    # 标准：中等得分
    if synthesize_score >= 4:
        return "normal"

    # 低分默认浅度
    return "shallow"


# ═══════════════════════════════════════════════════════════════════════════════
# 主分类函数
# ═══════════════════════════════════════════════════════════════════════════════


def classify_message(message: str, context: str = "") -> AutoDiscussDecision:
    """
    分析内阁群消息，决定是否触发讨论以及讨论参数。

    分类流程：
      1. NO_DISCUSS 过滤：检查是否命中不触发模式（问候、简单问答、已指定模式等）
      2. 双轨评分：计算 roleplay_score 和 synthesize_score
      3. 阈值判断：总分 < 3 → NO_DISCUSS
      4. 模式比较：高分者胜出；持平则倾向 SYNTHESIZE
      5. 参数派生：根据模式和得分确定 rounds / depth / persona / stance

    Args:
        message: 待分类的消息文本。
        context: 可选的上下文信息（群聊历史、前序消息等），用于辅助判断。
                 当前版本 context 暂未深度使用，预留接口供后续扩展。

    Returns:
        AutoDiscussDecision 对象，包含是否触发、模式、议题、轮数/深度等完整决策。

    Examples:
        >>> d = classify_message("三省六部架构该不该重构？")
        >>> d.should_discuss, d.mode, d.rounds
        (True, 'roleplay', 3)

        >>> d = classify_message("帮我分析一下 auth 模块的性能瓶颈")
        >>> d.should_discuss, d.mode, d.depth
        (True, 'synthesize', 'deep')

        >>> d = classify_message("你好")
        >>> d.should_discuss
        False

        >>> d = classify_message("怎么配置 nginx？")
        >>> d.should_discuss
        False
    """
    # ── 步骤 0：消息预处理 ──────────────────────────────────────────────────
    msg = message.strip()
    if not msg:
        return AutoDiscussDecision(
            should_discuss=False,
            reasoning="空消息，不触发讨论",
        )

    # ── 步骤 1：NO_DISCUSS 快速过滤 ─────────────────────────────────────────
    # 检查消息是否匹配任一不触发模式
    for pat in _NO_DISCUSS_RE:
        if pat.search(msg):
            return AutoDiscussDecision(
                should_discuss=False,
                topic=extract_topic(msg),
                reasoning=f"命中 NO_DISCUSS 过滤规则: 模式 '{pat.pattern}' 匹配",
            )

    # ── 步骤 2：双轨评分 ────────────────────────────────────────────────────
    rp_score = _score_roleplay(msg)
    sy_score = _score_synthesize(msg)
    total_score = rp_score + sy_score

    # ── 步骤 3：阈值判断 ────────────────────────────────────────────────────
    if total_score < 3:
        return AutoDiscussDecision(
            should_discuss=False,
            topic=extract_topic(msg),
            reasoning=(
                f"总分 {total_score} < 3（ROLEPLAY={rp_score}, SYNTHESIZE={sy_score}），"
                "未达到讨论触发阈值"
            ),
        )

    # ── 步骤 4：模式比较 ────────────────────────────────────────────────────
    topic = extract_topic(msg)

    if rp_score > sy_score:
        # roleplay 胜出
        mode = "roleplay"
        rounds = _determine_rounds(msg, rp_score)
        depth = ""
        reasoning = (
            f"ROLEPLAY 模式触发：ROLEPLAY 得分 {rp_score} > SYNTHESIZE 得分 {sy_score}。"
            f"识别到的辩论信号：{'二选一/应否判断' if rp_score >= 6 else '权衡/变更/方案对比'}。"
            f"建议 {rounds} 轮双边辩论。"
        )
    else:
        # synthesize 胜出（持平也走 synthesize）
        mode = "synthesize"
        rounds = 0
        depth = _determine_depth(msg, sy_score)
        reasoning = (
            f"SYNTHESIZE 模式触发：SYNTHESIZE 得分 {sy_score} >= ROLEPLAY 得分 {rp_score}。"
            f"识别到的分析信号：{'深度分析/产出请求' if sy_score >= 6 else '常规分析问题'}。"
            f"建议 {depth} 级研判。"
        )

    # ── 步骤 5：派生 persona 和 stance ──────────────────────────────────────
    regent_persona, initial_stance = _derive_persona_stance(msg, mode, topic)

    return AutoDiscussDecision(
        should_discuss=True,
        mode=mode,
        topic=topic,
        rounds=rounds,
        depth=depth,
        regent_persona=regent_persona,
        initial_stance=initial_stance,
        reasoning=reasoning,
    )


def _derive_persona_stance(
    message: str, mode: str, topic: str
) -> tuple[str, str]:
    """
    根据消息内容和模式，派生太子 persona 和小黄初始立场。

    当前版本提供简单启发式派生：
      - 若消息包含"重构"/"替换"/"迁移" → 太子主"变更"立场，小黄守"现有"
      - 若消息包含"该不该"/"要不要" → 太子主"变革"立场，小黄守"审慎"
      - 若消息包含"利弊"/"优劣" → 太子主"分析"立场，小黄守"质疑"
      - 默认：太子"审视全局"，小黄"独立补阙"

    Args:
        message: 消息文本。
        mode: 讨论模式。
        topic: 提取的议题。

    Returns:
        (regent_persona, initial_stance) 二元组。
    """
    # 技术变更类 → 太子主张重构，小黄守现有架构
    if any(re.search(p, message) for p in [
        r"重构", r"重写", r"替换", r"迁移", r"废除",
    ]):
        return (
            f"主张对「{topic}」进行变更，认为现有方案已不满足当前需求",
            f"主张审慎评估变更代价，维护现有架构的稳定性",
        )

    # 应否判断类 → 太子主张推进，小黄强调风险
    if any(re.search(p, message) for p in [
        r"该不该", r"要不要", r"是否该", r"应不应",
    ]):
        return (
            f"倾向于认为「{topic}」应当推进，陈述理由",
            f"从风险与成本角度审慎评估，提出可能被忽略的隐患",
        )

    # 利弊权衡类 → 太子综合评估，小黄挑战薄弱点
    if any(re.search(p, message) for p in [
        r"利弊", r"优劣", r"得失", r"权衡",
    ]):
        return (
            f"就「{topic}」综合评估各方利弊，提出倾向性判断",
            f"挑战太子分析中的薄弱环节，补充被低估的负面因素",
        )

    # synthesize 默认 persona
    if mode == "synthesize":
        return (
            f"就「{topic}」命小黄做独立深度研判，太子最终综合定夺",
            "",  # synthesize 模式下小黄无 stance，以独立分析者身份出现
        )

    # roleplay 通用默认
    return (
        f"就「{topic}」审视全局，提出三省六部立场",
        f"以独立秘书身份，从父皇视角补阙太子可能未察之关节",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 便捷函数 — 快速判断
# ═══════════════════════════════════════════════════════════════════════════════


def should_discuss(message: str) -> bool:
    """
    快速判断一条消息是否应该触发讨论。

    这是 classify_message 的快捷包装，仅返回布尔值。

    Args:
        message: 消息文本。

    Returns:
        是否应触发讨论。

    Examples:
        >>> should_discuss("架构重构方案对比")
        True
        >>> should_discuss("你好")
        False
    """
    return classify_message(message).should_discuss


def get_mode(message: str) -> str:
    """
    快速获取消息对应的讨论模式。

    Args:
        message: 消息文本。

    Returns:
        讨论模式字符串（"roleplay" / "synthesize" / ""）。

    Examples:
        >>> get_mode("重构 vs 重写，哪个更好？")
        'roleplay'
        >>> get_mode("帮我出一个技术评估报告")
        'synthesize'
    """
    return classify_message(message).mode


# ═══════════════════════════════════════════════════════════════════════════════
# 模块自测（直接运行时执行）
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    模块自测：运行一组手工 case，验证分类逻辑。
    执行方式：python -m auto_discuss 或 python auto_discuss.py
    """
    # 测试用例：(消息, 期望 should_discuss, 期望 mode)
    test_cases: list[tuple[str, bool, str]] = [
        # ROLEPLAY 应触发
        ("三省六部架构该不该重构？", True, "roleplay"),
        ("auth 模块：重写 vs 打补丁，哪个更好？", True, "roleplay"),
        ("利弊分析：替换 ORM 框架", True, "roleplay"),
        ("微服务 vs 单体架构，选哪个", True, "roleplay"),
        ("要不要启动 EmpireThread 项目", True, "roleplay"),
        ("方案 A 和方案 B 对比", True, "roleplay"),
        ("该不该现在升级到 v2？", True, "roleplay"),

        # SYNTHESIZE 应触发
        ("帮我分析一下 auth middleware 的性能瓶颈", True, "synthesize"),
        ("对当前系统做一次安全审计", True, "synthesize"),
        ("为什么最近延迟飙高了？", True, "synthesize"),
        ("给一个技术栈规划路线图", True, "synthesize"),
        ("调研一下市场上的替代方案", True, "synthesize"),
        ("如何优化数据库查询性能？", True, "synthesize"),
        ("做一次全面的技术盘点，出报告", True, "synthesize"),

        # NO_DISCUSS 不应触发
        ("你好", False, ""),
        ("早", False, ""),
        ("在吗", False, ""),
        ("怎么配置 nginx？", False, ""),
        ("X 是什么？", False, ""),
        ("查一下今天的日志", False, ""),
        ("帮我看看这个文件", False, ""),
        ("显示最近的提交", False, ""),
        ("晚安", False, ""),
        ("hi there", False, ""),
        ("ROLEPLAY 讨论一下架构", False, ""),    # 已指定模式
        ("SYNTHESIZE 分析安全风险", False, ""),    # 已指定模式
        ("朝议：三省六部精简方案", False, ""),     # 已指定模式

        # 边缘 case
        ("", False, ""),                            # 空消息
        ("列出所有 API 端点", False, ""),            # 单步指令
        ("怎么用这个工具？", False, ""),             # 简单操作问答
        ("用 React 还是 Vue？", True, "roleplay"),   # 🆕 简单二选一
        ("选 React 还是 Vue", True, "roleplay"),     # 🆕 无问号二选一
    ]

    print("=" * 70)
    print("AutoDiscuss 模块自测")
    print("=" * 70)

    passed = 0
    failed = 0

    for msg, expected_discuss, expected_mode in test_cases:
        decision = classify_message(msg)
        discuss_ok = decision.should_discuss == expected_discuss
        mode_ok = decision.mode == expected_mode if expected_discuss else True
        status = "✅" if (discuss_ok and mode_ok) else "❌"

        if discuss_ok and mode_ok:
            passed += 1
        else:
            failed += 1

        print(f"\n{status} 消息: 「{msg}」")
        print(f"   应触发: {decision.should_discuss} (期望 {expected_discuss})")
        if decision.should_discuss:
            print(f"   模式: {decision.mode} (期望 {expected_mode})")
            if decision.mode == "roleplay":
                print(f"   轮数: {decision.rounds}")
            else:
                print(f"   深度: {decision.depth}")
        print(f"   评分: ROLEPLAY={_score_roleplay(msg)}  "
              f"SYNTHESIZE={_score_synthesize(msg)}")
        print(f"   议题: 「{decision.topic}」")
        print(f"   推理: {decision.reasoning[:80]}...")

    print(f"\n{'=' * 70}")
    print(f"结果: {passed} 通过, {failed} 失败 (共 {len(test_cases)} 条)")
    print(f"{'=' * 70}")
