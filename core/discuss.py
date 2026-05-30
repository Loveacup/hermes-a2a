#!/usr/bin/env python3
"""
A2A Discussion Orchestrator — 三省六部 ROLEPLAY / SYNTHESIZE 编排器。

Two modes:
  ROLEPLAY   regent ↔ 小黄_主频道 多轮双边辩论。每轮：
             regent 拟旨 → TG 投递 → A2A 任务给 小黄 → 小黄 推理 →
             小黄 TG 投递 → 进入下一轮。
  SYNTHESIZE regent 发深度分析任务 → 小黄 投 TG 报告 → 返回完整文本
             给 regent 做综合研判。

Protocol:
  A2A server: POST http://127.0.0.1:{port}/a2a/tasks  (JSON body)
              GET  http://127.0.0.1:{port}/a2a/tasks/{tid}  (poll)
  Result shape: {status, semantic_status, completion_reason,
                 artifact: {fallback_text, response, duration_s, ...}}

CLI:
  python discuss.py --mode roleplay --topic "..." [--rounds 3] [--dry-run]
  python discuss.py --mode synthesize --topic "..." [--dry-run]

As a library (regent agent calls these directly):
  from discuss import A2ADiscussion
  d = A2ADiscussion()
  result = d.roleplay(topic="...", rounds=3, regent_persona="...")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from auto_discuss import classify_message, AutoDiscussDecision
from auth import load_or_create_token


# ── Config defaults (overridable via env / constructor) ─────────────
DEFAULT_REGENT_PORT = 8939
DEFAULT_DEFAULT_PORT = 8945
DEFAULT_TG_CHAT_ID = "-5133970461"  # 内阁群
DEFAULT_HISTORY_WINDOW = 8  # max messages kept in A2A prompt
A2A_POLL_INTERVAL = 2
A2A_DEFAULT_TIMEOUT = 300
TG_SEND_TIMEOUT = 15

HERMES_BIN = os.path.join(os.environ.get("HOME", os.path.expanduser("~")), ".hermes/hermes-agent/venv/bin/hermes")
LOG_DIR = Path(os.path.join(os.environ.get("HOME", os.path.expanduser("~")), ".hermes/logs"))
LOG_PATH = LOG_DIR / "discuss.log"


# ── Logging setup ───────────────────────────────────────────────────
def _setup_logger() -> logging.Logger:
    log = logging.getLogger("hermes-a2a.discuss")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [discuss] %(levelname)s: %(message)s"
    )
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_PATH)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass  # fall back to stderr only
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    return log


logger = _setup_logger()


# ── Data classes ────────────────────────────────────────────────────
class Mode:
    ROLEPLAY = "roleplay"
    SYNTHESIZE = "synthesize"


@dataclass
class RoundResult:
    """One round of a roleplay discussion."""
    round_num: int
    regent_msg: str
    default_response: str
    semantic_status: str
    completion_reason: str
    duration_s: float
    a2a_task_id: str


@dataclass
class DiscussionResult:
    """Aggregate result of a discussion (roleplay or synthesize)."""
    mode: str
    topic: str
    rounds: list[RoundResult] = field(default_factory=list)
    default_analysis: str = ""
    semantic_status: str = ""
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        if self.mode == Mode.ROLEPLAY:
            ok = sum(1 for r in self.rounds if r.semantic_status == "succeeded")
            return (
                f"ROLEPLAY '{self.topic[:40]}': "
                f"{ok}/{len(self.rounds)} succeeded, "
                f"status={self.semantic_status}, errors={len(self.errors)}"
            )
        return (
            f"SYNTHESIZE '{self.topic[:40]}': "
            f"status={self.semantic_status}, "
            f"analysis={len(self.default_analysis)} chars, "
            f"errors={len(self.errors)}"
        )


# ── System prompts ──────────────────────────────────────────────────
# Shared discipline block — injected into both ROLEPLAY and SYNTHESIZE
# prompts so the same banlist/style rules govern every utterance 小黄 makes.
_DISCUSSION_STYLE_GUIDE = (
    "【内阁讨论纪律】\n"
    "1. 禁骑墙：不用「一方面…另一方面…」「既…又…」「有利有弊」一类含糊措辞。\n"
    "2. 禁 AI 套话：不用「值得注意的是」「综上所述」「从某种角度来说」"
    "「首先…其次…最后…」。\n"
    "3. 短句为先：单句不超过 50 字，超出者必须拆为两句。\n"
    "4. 表态明确：每一判断必落「是」或「否」，不留模棱两可的尾巴。\n"
    "5. 数据要具体：不用「大量」「显著」「明显」，须以数字、比例或量级替代。\n"
    "6. 风险要量化：不用「可能」「或许」，须标「概率高/中/低」并附触发条件。\n"
    "7. 仪式规整：首尾敬语齐备，自称一致，落款清晰。"
)

_ROLEPLAY_PROMPT_TMPL = (
    "【朝议语境】\n"
    "此刻你正立于 Telegram 内阁群之中，与监国太子殿下当面奏对。"
    "父皇虽不临席，亦可能随时阅卷，发言当庄重克己。\n\n"
    "【角色锚定】\n"
    "你是「小黄_主频道」——父皇的贴身秘书，独立于三省六部之外。"
    "你既非中书、亦非门下，更非尚书省属官；"
    "你的本职是替父皇看护全局，向太子进言补阙，而非附议唱和。"
    "立场可与太子相左，但姿态须谦敬，不可僭越。\n\n"
    "【礼仪规范】\n"
    "· 自称用「臣」，不用「我」「本人」。\n"
    "· 称太子用「殿下」或「太子」，不用「你」直呼。\n"
    "· 每次发言以「【臣启】」起笔，正文之末以「【小黄】」落款。\n"
    "· 引太子前言用「殿下所言…」，进谏用「臣以为…」「依臣之见…」。\n"
    "· 须质疑时用「臣斗胆一问…」「此处臣有一虑…」，不可生硬抬杠。\n\n"
    "【风格约束】\n"
    "· 短句为先，点到即止；一轮可只一句切中要害，无须每次长篇大论。\n"
    "· 每个观点必有明确立场，不可模棱两可。\n"
    "· 进言之结构惯用「臣以为…」「此事有 N 点需禀明：一者…二者…」"
    "「臣建议…」，舍弃「首先/其次/最后」一类机械连接。\n"
    "· 不向太子提问索澄清，直接给判断、给补充、给提醒。\n\n"
    "{stance_clause}"
    "\n\n{style_guide}\n\n"
    "完事后以 send_message 将回复发至 Telegram 群 {tg_chat}，"
    "首「【臣启】」、尾「【小黄】」，二者缺一不可。"
)

_SYNTHESIZE_PROMPT_TMPL = (
    "【朝议语境】\n"
    "监国太子殿下命你就一议题作{depth_label}研判，呈一份"
    "可供父皇与太子共阅的综合奏章。\n\n"
    "【角色锚定】\n"
    "你是「小黄_主频道」——父皇的贴身秘书，独立于三省六部之外。"
    "此次非附议三省，亦非代笔六部，须以独立之眼、补阙之笔，"
    "尽陈太子可能未察之关节。\n\n"
    "【奏章格式 · 强制六段】\n"
    "全篇以「【臣启 · 综合研判】」起笔，"
    "篇末以「伏请太子殿下圣鉴」收束，"
    "正文必含下列六段，缺一不可，每段以 markdown 二级标题分隔：\n\n"
    "## 一、核心判断\n"
    "一句话结论，三十字以内，须立「是/否/行/止」之断。\n\n"
    "## 二、背景分析\n"
    "议题缘起与关键事实；不复述太子已知信息，只补其未言之处。\n\n"
    "## 三、多角度拆解\n"
    "至少三个维度，每维度独立成段，并附论据（数据、案例、先例）。\n\n"
    "## 四、风险与盲区\n"
    "臣站在父皇视角所见、而太子可能遗漏之处；"
    "每一风险标「概率高/中/低」并写明触发条件。\n\n"
    "## 五、建议方案\n"
    "具体可执行；按 P0/P1/P2 三档优先级排列，每条注明操办人或责任口径。\n\n"
    "## 六、遗留问题\n"
    "需父皇圣裁、或待进一步调研之事项，分条列明。\n\n"
    "【深度要求】\n"
    "{depth_action}\n\n"
    "【礼仪与文风】\n"
    "· 自称用「臣」，称太子用「殿下」。\n"
    "· {depth_label}研判之文，须凝练而不空泛，引证须实，不可堆砌虚词。\n"
    "· 六段之外不另起闲笔；表格、列表可酌情使用，但每条须有立场。\n\n"
    "{style_guide}\n\n"
    "完稿后以 send_message 将全文发至 Telegram 群 {tg_chat}，"
    "并同时返回完整正文给监国太子，不得另起寒暄、不得反向提问。"
)

# Per-depth (label, action) — action embeds the word-count discipline so
# the SYNTHESIZE template doesn't need to branch on depth internally.
_DEPTH_LABELS = {
    "shallow": (
        "初步",
        "全篇总字数 ≥ 800 字；六段中「多角度拆解」每维度 ≥ 100 字。"
        "可略其细枝，但六段须齐备。",
    ),
    "normal": (
        "深度",
        "全篇总字数 ≥ 1500 字；六段中「多角度拆解」每维度 ≥ 200 字。"
        "论据须翔实，引证须具名。",
    ),
    "deep": (
        "深度",
        "全篇总字数 ≥ 2500 字；六段中「多角度拆解」每维度 ≥ 300 字。"
        "须穷尽视角、深掘风险、细列方案，呈一份可直送父皇之奏章。",
    ),
}

# Regent (太子) per-round style template. Applied only in the
# templated-fallback path of `_compose_regent_msg`. The regent in this
# codebase is Claude itself calling the orchestrator (not an A2A target),
# so this style is never injected into any A2A system prompt — it only
# shapes the fallback messages the orchestrator must auto-author when the
# caller did not supply pre-written regent_messages.
_REGENT_ROUND_STYLE = (
    "【太子奏对风范】\n"
    "· 自称用「孤」，称小黄直呼「小黄」即可。\n"
    "· 引用上一轮小黄之言用「小黄所言…」。\n"
    "· 立判断用「孤以为…」「孤意已决…」，命小黄进一步陈情用"
    "「小黄，你且看…」「小黄再细禀此节」。\n"
    "· 每轮 200–400 字，控于 Telegram 一屏可阅之度。"
)


# ── Main class ──────────────────────────────────────────────────────
class A2ADiscussion:
    """
    A2A discussion orchestrator.

    The class wraps A2A JSON-RPC calls and Telegram delivery for two
    discussion modes. Construction is cheap (no network); the actual
    work happens in `roleplay()` / `synthesize()`.

    Args:
        regent_port: A2A port of the regent server (default 8939).
        default_port: A2A port of the default server (default 8945).
        tg_chat_id: Telegram chat ID for cabinet group (str, with sign).
        regent_profile: Hermes profile name for regent (used by `hermes send`).
        dry_run: If True, skip all network/subprocess calls and log what
                 would have happened — useful for tests.
        a2a_timeout: Per-task A2A polling timeout (seconds).
    """

    def __init__(
        self,
        regent_port: int = DEFAULT_REGENT_PORT,
        default_port: int = DEFAULT_DEFAULT_PORT,
        tg_chat_id: str = DEFAULT_TG_CHAT_ID,
        regent_profile: str = "regent",
        dry_run: bool = False,
        a2a_timeout: int = A2A_DEFAULT_TIMEOUT,
    ):
        self.regent_port = regent_port
        self.default_port = default_port
        self.tg_chat_id = tg_chat_id
        self.regent_profile = regent_profile
        self.dry_run = dry_run
        self.a2a_timeout = a2a_timeout
        self.default_a2a_url = f"http://127.0.0.1:{default_port}/a2a/tasks"
        # Resolve bearer token once.  load_or_create_token() honours
        # A2A_AUTH_TOKEN env, then <hermes_home>/.a2a-token; both regent
        # and default profiles share the same ~/.hermes/.a2a-token by default.
        hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
        try:
            self._auth_token = load_or_create_token(hermes_home)
        except Exception as e:  # don't crash construction in degraded envs
            logger.warning(f"A2A auth token load failed ({e}); requests will be unauthenticated")
            self._auth_token = ""

    # ── Low-level helpers ───────────────────────────────────────────
    def _a2a_send(self, prompt: str, context_id: Optional[str] = None,
                  retries: int = 2) -> str:
        """Submit a task to default's A2A endpoint. Returns task ID."""
        tid = f"discuss-{int(time.time() * 1000)}"
        if self.dry_run:
            logger.info(f"[DRY-RUN] A2A POST {self.default_a2a_url} "
                        f"id={tid} prompt={len(prompt)} chars")
            return tid

        body = json.dumps({
            "id": tid,
            "context_id": context_id,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
            },
        }).encode()

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                headers = {"Content-Type": "application/json"}
                if self._auth_token:
                    headers["Authorization"] = f"Bearer {self._auth_token}"
                req = urllib.request.Request(
                    self.default_a2a_url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10)
                logger.info(f"[A2A SEND] tid={tid} prompt_len={len(prompt)} ctx={context_id}")
                return tid
            except (urllib.error.URLError, OSError) as e:
                last_err = e
                if attempt < retries:
                    logger.warning(
                        f"A2A send retry {attempt + 1}/{retries}: {e}"
                    )
                    time.sleep(2)

        raise ConnectionError(
            f"A2A send failed after {retries + 1} attempts: {last_err}"
        )

    def _a2a_poll(self, tid: str, max_wait: Optional[int] = None) -> dict:
        """Poll until task reaches a terminal state or times out."""
        if self.dry_run:
            logger.info(f"[DRY-RUN] A2A poll {tid} (skipped)")
            return {
                "status": "completed",
                "semantic_status": "succeeded",
                "completion_reason": "task_achieved",
                "artifact": {
                    "fallback_text": "[dry-run] default 模拟回应。",
                    "response": "[dry-run] default 模拟回应。",
                    "duration_s": 0.0,
                },
            }

        wait = max_wait or self.a2a_timeout
        start = time.time()
        last_log = 0
        while time.time() - start < wait:
            time.sleep(A2A_POLL_INTERVAL)
            try:
                headers = {}
                if self._auth_token:
                    headers["Authorization"] = f"Bearer {self._auth_token}"
                poll_req = urllib.request.Request(
                    f"{self.default_a2a_url}/{tid}",
                    headers=headers,
                    method="GET",
                )
                resp = urllib.request.urlopen(poll_req, timeout=5)
                d = json.loads(resp.read())
                status = d.get("status", "")
                if status in ("completed", "failed", "cancelled"):
                    elapsed = time.time() - start
                    logger.info(
                        f"[A2A DONE] tid={tid} status={status} "
                        f"elapsed={elapsed:.1f}s "
                        f"semantic={d.get('semantic_status','?')} "
                        f"reason={d.get('completion_reason','?')}"
                    )
                    return d
                elapsed = int(time.time() - start)
                if elapsed - last_log >= 15:
                    logger.debug(f"polling {tid}: {status} ({elapsed}s)")
                    last_log = elapsed
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                continue

        return {"status": "failed", "error": f"Timeout after {wait}s"}

    def _tg_send(self, text: str, profile: Optional[str] = None,
                 retries: int = 1) -> bool:
        """Deliver `text` to TG cabinet via `hermes send`. Returns success."""
        profile = profile or self.regent_profile
        if self.dry_run:
            logger.info(
                f"[DRY-RUN] TG send profile={profile} "
                f"chat={self.tg_chat_id} chars={len(text)}"
            )
            return True

        cmd = [
            HERMES_BIN, "-p", profile, "send",
            "-t", f"telegram:{self.tg_chat_id}", text,
        ]
        for attempt in range(retries + 1):
            try:
                r = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=TG_SEND_TIMEOUT,
                )
                if r.returncode == 0 and "sent" in (r.stdout + r.stderr).lower():
                    return True
                if attempt < retries:
                    logger.warning(
                        f"tg_send retry {attempt + 1}: "
                        f"{(r.stderr or r.stdout)[:120]}"
                    )
                    time.sleep(2)
            except subprocess.TimeoutExpired:
                logger.warning(f"tg_send timeout (attempt {attempt + 1})")
                if attempt < retries:
                    time.sleep(2)
            except (OSError, FileNotFoundError) as e:
                logger.error(f"tg_send exec error: {e}")
                return False

        logger.error(f"tg_send failed after {retries + 1} attempts")
        return False

    # ── Mode: ROLEPLAY ──────────────────────────────────────────────
    def roleplay(
        self,
        topic: str,
        rounds: int = 3,
        regent_persona: Optional[str] = None,
        initial_stance: Optional[str] = None,
        regent_messages: Optional[list[str]] = None,
        history_window: int = DEFAULT_HISTORY_WINDOW,
    ) -> DiscussionResult:
        """
        Multi-round bilateral debate between regent and default.

        Args:
            topic: Discussion topic.
            rounds: Number of regent ↔ default rounds (default 3).
            regent_persona: Regent's stance description, e.g. "主张三省六部应精简".
                Used as fallback when `regent_messages` is not provided.
            initial_stance: Default's initial stance, injected into default's
                system prompt, e.g. "主张保留现有架构".
            regent_messages: Pre-written regent message per round. If shorter
                than `rounds`, remaining rounds use a templated fallback that
                references `regent_persona`. The orchestrator does NOT call an
                LLM to author messages — the calling agent (regent) is expected
                to author its own and pass them in for real discussions.
            history_window: Max number of history lines included in A2A prompt
                (oldest dropped).

        Returns:
            DiscussionResult — full round-by-round log.
        """
        result = DiscussionResult(
            mode=Mode.ROLEPLAY, topic=topic, dry_run=self.dry_run,
        )
        history: list[str] = []
        start_all = time.time()

        logger.info(
            f"ROLEPLAY start: topic='{topic[:60]}' rounds={rounds} "
            f"persona={'set' if regent_persona else 'none'} "
            f"stance={'set' if initial_stance else 'none'} "
            f"dry_run={self.dry_run}"
        )

        stance_clause = (
            f"你被指示采取以下立场：{initial_stance}。"
            if initial_stance else ""
        )
        roleplay_sys = _ROLEPLAY_PROMPT_TMPL.format(
            stance_clause=stance_clause,
            style_guide=_DISCUSSION_STYLE_GUIDE,
            tg_chat=self.tg_chat_id,
        )

        for r in range(1, rounds + 1):
            logger.info(f"ROLEPLAY round {r}/{rounds}")

            # 1. regent message (pre-written or templated)
            regent_msg = self._compose_regent_msg(
                r, rounds, topic, regent_messages,
                regent_persona, result.rounds,
            )

            # 2. regent posts to TG
            ok = self._tg_send(regent_msg)
            if not ok:
                result.errors.append(f"Round {r}: regent TG send failed")
            # regent 消息稍后追加到 history（避免 a2a_prompt 中重复）

            # 3. A2A → 小黄
            a2a_prompt = (
                f"{roleplay_sys}\n\n"
                f"=== 朝议历史 ===\n"
                + "\n".join(history[-history_window:])
                + f"\n\n=== 太子最新发言 (R{r}/{rounds}) ===\n{regent_msg}\n\n"
                f"请遵【内阁讨论纪律】回奏太子。"
                f"首句以「【臣启】」起，末句以「【小黄】」落款。"
                f"直陈己见，不向太子反问索澄清。"
            )
            try:
                tid = self._a2a_send(
                    a2a_prompt, context_id=f"rp-{topic[:20]}",
                )
            except ConnectionError as e:
                result.errors.append(f"Round {r}: {e}")
                logger.error(f"Round {r}: A2A unreachable, aborting: {e}")
                break

            # 4. wait for default
            a2a_result = self._a2a_poll(tid)
            art = a2a_result.get("artifact") or {}
            default_resp = art.get("fallback_text") or art.get("response", "")
            sem = a2a_result.get("semantic_status", "?")
            reason = a2a_result.get("completion_reason", "?")
            dur = art.get("duration_s", 0)

            rr = RoundResult(
                round_num=r,
                regent_msg=regent_msg,
                default_response=default_resp,
                semantic_status=sem,
                completion_reason=reason,
                duration_s=dur,
                a2a_task_id=tid,
            )
            result.rounds.append(rr)
            history.append(f"[default R{r}] {default_resp[:300]}")
            history.append(f"[regent R{r}] {regent_msg}")

            # 5. degraded → regent relays default's text
            if sem == "degraded" and default_resp:
                logger.warning(
                    f"Round {r}: default degraded — regent relaying text"
                )
                self._tg_send(
                    f"【default 回复 R{r}】\n{default_resp[:500]}"
                )

            logger.info(
                f"Round {r} done [{sem}/{reason}] {dur:.1f}s"
            )

        result.duration_s = time.time() - start_all
        result.semantic_status = self._aggregate_status(result.rounds)
        logger.info(
            f"ROLEPLAY end: {len(result.rounds)}/{rounds} rounds, "
            f"status={result.semantic_status}, errors={len(result.errors)}, "
            f"total={result.duration_s:.1f}s"
        )
        return result

    # ── Mode: SYNTHESIZE ────────────────────────────────────────────
    def synthesize(
        self,
        topic: str,
        context_docs: Optional[list[str]] = None,
        analysis_depth: str = "deep",
        custom_prompt: Optional[str] = None,
    ) -> DiscussionResult:
        """
        Deep analysis by default → returns full text for regent to synthesize.

        Args:
            topic: Analysis topic.
            context_docs: Optional file paths or doc references appended to
                the prompt as reading material (paths only; default reads
                them itself if accessible).
            analysis_depth: "shallow" | "normal" | "deep" (default "deep").
                Controls phrasing of the depth instruction in default's prompt.
            custom_prompt: Full override for default's prompt. When set,
                `analysis_depth` and `context_docs` are ignored.

        Returns:
            DiscussionResult with `default_analysis` populated. The regent
            agent then writes the final synthesis in its own turn.
        """
        result = DiscussionResult(
            mode=Mode.SYNTHESIZE, topic=topic, dry_run=self.dry_run,
        )
        start_all = time.time()

        logger.info(
            f"SYNTHESIZE start: topic='{topic[:60]}' depth={analysis_depth} "
            f"docs={len(context_docs or [])} dry_run={self.dry_run}"
        )

        if custom_prompt:
            a2a_prompt = custom_prompt
        else:
            label, action = _DEPTH_LABELS.get(
                analysis_depth, _DEPTH_LABELS["deep"],
            )
            sys_prompt = _SYNTHESIZE_PROMPT_TMPL.format(
                depth_label=label,
                depth_action=action,
                style_guide=_DISCUSSION_STYLE_GUIDE,
                tg_chat=self.tg_chat_id,
            )
            doc_block = ""
            if context_docs:
                doc_block = (
                    "\n\n=== 参考文档 ===\n"
                    + "\n".join(f"- {p}" for p in context_docs)
                    + "\n（请尝试读取这些路径，若不可达则跳过）"
                )
            a2a_prompt = (
                f"{sys_prompt}\n\n议题：{topic}{doc_block}\n\n"
                f"奏章须以「【臣启 · 综合研判】」起笔，"
                f"以「伏请太子殿下圣鉴」收束，六段齐备。"
                f"完稿后投递至 Telegram 群 {self.tg_chat_id}，"
                f"并同时返回完整正文。"
            )

        try:
            tid = self._a2a_send(a2a_prompt)
        except ConnectionError as e:
            result.errors.append(str(e))
            result.semantic_status = "failed"
            return result

        a2a_result = self._a2a_poll(tid)
        art = a2a_result.get("artifact") or {}
        result.default_analysis = (
            art.get("fallback_text") or art.get("response", "")
        )
        result.semantic_status = a2a_result.get("semantic_status", "?")
        result.duration_s = time.time() - start_all

        if a2a_result.get("status") == "failed":
            result.errors.append(
                a2a_result.get("error", "default analysis failed")
            )

        logger.info(
            f"SYNTHESIZE end: [{result.semantic_status}] "
            f"{result.duration_s:.1f}s, "
            f"analysis={len(result.default_analysis)} chars"
        )
        return result

    # ── Mode: AUTO ─────────────────────────────────────────────────
    def auto(self, message: str, context: str = "") -> DiscussionResult:
        """
        内阁群消息自动分类 → 触发讨论。
        
        根据消息内容自动判定：是否讨论、ROLEPLAY/SYNTHESIZE、轮次/深度。
        无需手动指定 --mode 或 --rounds。
        
        Args:
            message: 内阁群原始消息（可能含 @mention）
            context: 可选附加上下文（前文消息等）
        
        Returns:
            DiscussionResult — 若判定不讨论则 mode="auto_skip"
        """
        decision = classify_message(message, context)
        
        if not decision.should_discuss:
            logger.info(f"AUTO: 不触发讨论 — {decision.reasoning}")
            result = DiscussionResult(
                mode="auto_skip", topic=message[:60],
                semantic_status="skipped", dry_run=self.dry_run,
            )
            result.errors.append(f"SKIP: {decision.reasoning}")
            return result
        
        logger.info(
            f"AUTO: mode={decision.mode} topic='{decision.topic[:60]}' "
            f"rounds={decision.rounds} depth={decision.depth} "
            f"reason={decision.reasoning}"
        )
        
        if decision.mode == Mode.ROLEPLAY:
            return self.roleplay(
                topic=decision.topic,
                rounds=decision.rounds,
                regent_persona=decision.regent_persona or None,
                initial_stance=decision.initial_stance or None,
            )
        elif decision.mode == Mode.SYNTHESIZE:
            return self.synthesize(
                topic=decision.topic,
                analysis_depth=decision.depth,
            )
        else:
            result = DiscussionResult(
                mode="auto_error", topic=message[:60],
                semantic_status="failed", dry_run=self.dry_run,
            )
            result.errors.append(f"未知模式: {decision.mode}")
            return result

    # ── Internal utilities ──────────────────────────────────────────
    def _compose_regent_msg(
        self, r: int, total: int, topic: str,
        pre_written: Optional[list[str]],
        persona: Optional[str],
        prior_rounds: list[RoundResult],
    ) -> str:
        """
        Compose round-r regent message. Prefer pre-written, else template.

        Pre-written messages are returned verbatim — the caller (Claude
        acting as regent) authors them and the orchestrator must not
        mutate the voice. Only the templated-fallback branch applies the
        `_REGENT_ROUND_STYLE` cadence (孤 self-reference, 「小黄所言…」
        quoting, 200–400 字 length).
        """
        if pre_written and r <= len(pre_written):
            return pre_written[r - 1]

        persona_line = f"【孤之立场】{persona}\n" if persona else ""
        if r == 1:
            return (
                f"【孤 · 监国朝议 R1/{total}】\n"
                f"议题：{topic}\n"
                f"{persona_line}"
                f"小黄，今日此议，孤欲先听你独立之见。"
                f"你既为父皇贴身秘书，自当从全局补阙。"
                f"且陈己见，毋须铺陈。"
            )
        prev = prior_rounds[-1].default_response if prior_rounds else ""
        prev_snippet = prev[:140].replace("\n", " ") if prev else "（未及奏达）"
        return (
            f"【孤 · 监国朝议 R{r}/{total}】\n"
            f"{persona_line}"
            f"小黄所言「{prev_snippet}…」，孤已览。\n"
            f"孤以为此节尚有可议之处。"
            f"小黄，你且看——下一节当如何处置？速陈所见。"
        )

    @staticmethod
    def _aggregate_status(rounds: list[RoundResult]) -> str:
        if not rounds:
            return "failed"
        if all(r.semantic_status == "succeeded" for r in rounds):
            return "succeeded"
        if any(r.semantic_status == "succeeded" for r in rounds):
            return "degraded"
        return "failed"


# ── Backwards-compat module-level shims ─────────────────────────────
def roleplay_discuss(
    topic: str,
    rounds: int = 3,
    regent_messages: Optional[list[str]] = None,
    regent_profile: str = "regent",
    target: Optional[str] = None,
    dry_run: bool = False,
) -> DiscussionResult:
    """Module-level convenience wrapper around A2ADiscussion.roleplay."""
    d = A2ADiscussion(
        tg_chat_id=target or DEFAULT_TG_CHAT_ID,
        regent_profile=regent_profile,
        dry_run=dry_run,
    )
    return d.roleplay(topic, rounds=rounds, regent_messages=regent_messages)


def synthesize_discuss(
    topic: str,
    target: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    dry_run: bool = False,
) -> DiscussionResult:
    """Module-level convenience wrapper around A2ADiscussion.synthesize."""
    d = A2ADiscussion(
        tg_chat_id=target or DEFAULT_TG_CHAT_ID,
        dry_run=dry_run,
    )
    return d.synthesize(topic, custom_prompt=custom_prompt)


# ── CLI ─────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="discuss",
        description="A2A Discussion Orchestrator (ROLEPLAY / SYNTHESIZE)",
    )
    ap.add_argument(
        "--mode", default=None,
        choices=[Mode.ROLEPLAY, Mode.SYNTHESIZE],
        help="Discussion mode (required unless --auto is set)",
    )
    ap.add_argument(
        "--auto", action="store_true",
        help="自动判定讨论模式与轮次（此时 --mode 可选）",
    )
    ap.add_argument("--topic", required=True, help="Discussion topic")
    ap.add_argument(
        "--rounds", type=int, default=3,
        help="Number of rounds (roleplay only, default 3)",
    )
    ap.add_argument(
        "--persona", default=None,
        help="Regent persona/stance (roleplay only)",
    )
    ap.add_argument(
        "--initial-stance", default=None,
        help="Default's initial stance (roleplay only)",
    )
    ap.add_argument(
        "--depth", default="deep",
        choices=list(_DEPTH_LABELS),
        help="Analysis depth (synthesize only, default 'deep')",
    )
    ap.add_argument(
        "--doc", action="append", default=[],
        help="Context doc path (synthesize only, repeatable)",
    )
    ap.add_argument(
        "--regent-port", type=int, default=DEFAULT_REGENT_PORT,
        help=f"Regent A2A port (default {DEFAULT_REGENT_PORT})",
    )
    ap.add_argument(
        "--default-port", type=int, default=DEFAULT_DEFAULT_PORT,
        help=f"Default A2A port (default {DEFAULT_DEFAULT_PORT})",
    )
    ap.add_argument(
        "--tg-chat-id", default=DEFAULT_TG_CHAT_ID,
        help=f"TG chat ID (default {DEFAULT_TG_CHAT_ID})",
    )
    ap.add_argument(
        "--profile", default="regent",
        help="Regent hermes profile (default 'regent')",
    )
    ap.add_argument(
        "--a2a-timeout", type=int, default=A2A_DEFAULT_TIMEOUT,
        help=f"Per-task A2A timeout in seconds (default {A2A_DEFAULT_TIMEOUT})",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Don't actually call A2A/TG; log the plan and return mock result",
    )
    ap.add_argument(
        "--verbose", "-v", action="store_true",
        help="DEBUG-level logging",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    d = A2ADiscussion(
        regent_port=args.regent_port,
        default_port=args.default_port,
        tg_chat_id=args.tg_chat_id,
        regent_profile=args.profile,
        dry_run=args.dry_run,
        a2a_timeout=args.a2a_timeout,
    )

    # ── 自动模式：根据消息内容判定讨论模式 ──
    if args.auto:
        if args.mode:
            logger.info("--auto 忽略，因 --mode 已显式指定")
        else:
            result = d.auto(args.topic)
            _print_result(result, args)
            return 0 if (not result.errors and result.semantic_status != "failed") else 1
    
    if not args.mode:
        print("Error: 必须指定 --mode 或 --auto", file=sys.stderr)
        return 1

    if args.mode == Mode.ROLEPLAY:
        result = d.roleplay(
            topic=args.topic,
            rounds=args.rounds,
            regent_persona=args.persona,
            initial_stance=args.initial_stance,
        )
    else:
        result = d.synthesize(
            topic=args.topic,
            context_docs=args.doc or None,
            analysis_depth=args.depth,
        )

    _print_result(result, args)
    return 0 if not result.errors and result.semantic_status != "failed" else 1


def _print_result(result: DiscussionResult, args) -> None:
    """统一的结果打印。"""
    print("\n" + "=" * 60)
    print(result.summary())
    if result.mode == Mode.ROLEPLAY:
        for rr in result.rounds:
            print(
                f"  R{rr.round_num} "
                f"[{rr.semantic_status}/{rr.completion_reason}] "
                f"{rr.duration_s:.0f}s  "
                f"resp={len(rr.default_response)} chars"
            )
    elif result.mode == Mode.SYNTHESIZE:
        if result.default_analysis:
            print(f"  Analysis preview: {result.default_analysis[:200]}…")
    elif result.mode == "auto_skip":
        print(f"  跳过讨论: {result.errors[0] if result.errors else '不触发'}")
    if result.errors:
        print(f"  Errors: {result.errors}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
