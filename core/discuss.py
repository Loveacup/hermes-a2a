#!/usr/bin/env python3
"""
A2A Discussion Orchestrator — multi-round, multi-profile discussions.

Two modes:
  ROLEPLAY   — regent + default post independently to Telegram 内阁群,
               creating visible back-and-forth (Telegram bots cannot
               see each other's messages — this mode bridges the gap).
  SYNTHESIZE — regent sends deep question to default via A2A,
               default responds with analysis, regent synthesizes
               unified report for the cabinet.

Architecture:
  discuss.py    — orchestration engine (this file)
  discuss-modes.yaml — per-deployment config (s6m-config/)
  a2a-discussion skill — regent's usage guide

Usage:
  # CLI (standalone, template-driven)
  python core/discuss.py roleplay "Should we do dispatcher or audits first?"
  python core/discuss.py synthesize "Analyze EmpireThread design risks"

  # As library (regent agent calls these functions directly)
  from discuss import roleplay_discuss, synthesize_discuss, Mode
  result = roleplay_discuss(topic="...", rounds=3)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("hermes-a2a.discuss")

# ── Config (overridable via env) ────────────────────────────────────
DEFAULT_A2A_URL = "http://127.0.0.1:8945/a2a/tasks"
DEFAULT_TG_CHANNEL = "-5133970461"  # 内阁群
HERMES_BIN = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes")

A2A_URL = os.environ.get("A2A_DISCUSS_URL", DEFAULT_A2A_URL)
A2A_TIMEOUT = int(os.environ.get("A2A_DISCUSS_TIMEOUT", "300"))
A2A_POLL_INTERVAL = 2  # seconds between polls
TG_SEND_TIMEOUT = 15  # seconds for hermes send


# ── Data classes ────────────────────────────────────────────────────
class Mode:
    ROLEPLAY = "roleplay"
    SYNTHESIZE = "synthesize"


@dataclass
class RoundResult:
    """Single round of a roleplay discussion."""
    round_num: int
    regent_msg: str
    default_response: str
    semantic_status: str
    completion_reason: str
    duration_s: float
    a2a_task_id: str


@dataclass
class DiscussionResult:
    """Complete discussion result."""
    mode: str
    topic: str
    rounds: list[RoundResult] = field(default_factory=list)
    default_analysis: str = ""
    semantic_status: str = ""
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── A2A helpers ─────────────────────────────────────────────────────
def _a2a_send(prompt: str, context_id: Optional[str] = None,
              retries: int = 2) -> str:
    """Send task to A2A endpoint. Returns task ID. Retries on failure."""
    tid = f"discuss-{int(time.time() * 1000)}"
    body = json.dumps({
        "id": tid,
        "context_id": context_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": prompt}],
        },
    }).encode()

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                A2A_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            return tid
        except Exception as e:
            last_err = e
            if attempt < retries:
                logger.warning(f"A2A send retry {attempt+1}/{retries}: {e}")
                time.sleep(2)

    raise ConnectionError(f"A2A send failed after {retries+1} attempts: {last_err}")


def _a2a_poll(tid: str, max_wait: int = A2A_TIMEOUT) -> dict:
    """Poll A2A task until completion or timeout. Returns full task dict."""
    start = time.time()
    last_log = 0
    while time.time() - start < max_wait:
        time.sleep(A2A_POLL_INTERVAL)
        try:
            resp = urllib.request.urlopen(f"{A2A_URL}/{tid}", timeout=5)
            d = json.loads(resp.read())
            status = d.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                return d
            # Progress logging every 15s
            elapsed = int(time.time() - start)
            if elapsed - last_log >= 15:
                logger.debug(f"polling {tid}: {status} ({elapsed}s)")
                last_log = elapsed
        except Exception:
            continue

    return {"status": "failed", "error": f"Timeout after {max_wait}s"}


def _tg_send(profile: str, text: str, target: Optional[str] = None,
             retries: int = 1) -> bool:
    """Post message to Telegram via hermes send CLI. Retries on failure."""
    tg = target or DEFAULT_TG_CHANNEL
    cmd = [HERMES_BIN, "-p", profile, "send", "-t", f"telegram:{tg}", text]

    for attempt in range(retries + 1):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=TG_SEND_TIMEOUT)
            if r.returncode == 0 and "sent" in (r.stdout + r.stderr).lower():
                return True
            if attempt < retries:
                logger.warning(f"tg_send retry {attempt+1}: {r.stderr[:100]}")
                time.sleep(2)
        except subprocess.TimeoutExpired:
            logger.warning(f"tg_send timeout (attempt {attempt+1})")
            if attempt < retries:
                time.sleep(2)

    logger.error(f"tg_send failed after {retries+1} attempts")
    return False


# ── System prompts (inject discussion context) ──────────────────────
_ROLEPLAY_PROMPT = (
    "【任务委托】监国太子（regent）请你协助讨论一个议题。
"
    "你的身份：小黄（主频道助手），Alex 的个人助理，独立于三省六部体系之外。
"
    "你正在与监国太子在 Telegram 内阁群进行工作讨论。
"
    "太子代表三省六部全体发言，你作为独立助手提供自己的视角。
"
    "每次收到太子的发言后，请直接回应——不要问问题，不要要求澄清，
"
    "直接给出你的分析、建议或补充。讨论风格：专业、直接、有建设性。
"
    "用 send_message 把回复发到 Telegram 群，
"
    "落款【小黄】。"
)

_SYNTHESIZE_PROMPT = (
    "【任务委托】监国太子（regent）请你对一个议题做深度分析。
"
    "你的身份：小黄（主频道助手），Alex 的个人助理，独立于三省六部体系之外。
"
    "请全面分析：背景、多角度观点、利弊权衡、建议方案。
"
    "分析完成后用 send_message 发到 Telegram 群。
"
    "同时直接返回你的完整分析文本。不要问我任何问题。"
)


# ── Mode: ROLEPLAY ──────────────────────────────────────────────────
def roleplay_discuss(
    topic: str,
    rounds: int = 3,
    regent_messages: Optional[list[str]] = None,
    regent_profile: str = "regent",
    target: Optional[str] = None,
) -> DiscussionResult:
    """
    Multi-round roleplay discussion between regent and default.

    Flow per round:
      1. regent posts message to TG
      2. regent sends A2A task to default (with full discussion history)
      3. default responds → posts to TG
      4. (next round) regent reads default's response, formulates counter

    Args:
        topic: Discussion topic
        rounds: Number of back-and-forth rounds
        regent_messages: Pre-written regent messages (one per round).
            If omitted, uses template placeholders. For real discussions,
            the regent agent should provide its own messages.
        regent_profile: Hermes profile name for regent
        target: Telegram channel ID (default: 内阁群)

    Returns:
        DiscussionResult with full round log
    """
    tg = target or DEFAULT_TG_CHANNEL
    result = DiscussionResult(mode=Mode.ROLEPLAY, topic=topic)
    history: list[str] = []

    logger.info(f"ROLEPLAY start: topic='{topic[:60]}' rounds={rounds}")

    for r in range(1, rounds + 1):
        logger.info(f"ROLEPLAY round {r}/{rounds}")

        # --- 1. regent formulates & posts ---
        if regent_messages and r <= len(regent_messages):
            regent_msg = regent_messages[r - 1]
        elif r == 1:
            regent_msg = (
                f"【太子 · 三省六部】\n议题：{topic}\n\n"
                f"开个讨论。我先说我的初步判断——（请在内阁群回应）"
            )
        else:
            prev = result.rounds[-1].default_response if result.rounds else ""
            regent_msg = (
                f"【太子 · 三省六部】\n"
                f"收到。关于上一轮你提到的观点，我的进一步看法是——"
            )

        ok = _tg_send(regent_profile, regent_msg, tg)
        if not ok:
            result.errors.append(f"Round {r}: regent TG send failed")
        history.append(f"[regent R{r}] {regent_msg}")

        # --- 2. Send A2A to default ---
        a2a_prompt = (
            f"{_ROLEPLAY_PROMPT}\n\n"
            f"=== 讨论历史 ===\n"
            + "\n".join(history[-8:])
            + f"\n\n=== 太子最新发言 (R{r}) ===\n{regent_msg}\n\n"
            f"请回应太子的发言。直接说你的观点，不要提问。落款【小黄】。"
        )
        try:
            tid = _a2a_send(a2a_prompt, context_id=f"rp-{topic[:20]}")
        except ConnectionError as e:
            result.errors.append(f"Round {r}: {e}")
            break

        # --- 3. Wait for default ---
        a2a_result = _a2a_poll(tid)
        art = a2a_result.get("artifact", {})
        default_resp = art.get("fallback_text", "") or art.get("response", "")
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

        # Degraded fallback: regent forwards default's text
        if sem == "degraded":
            logger.warning(f"Round {r}: default degraded — regent forwarding")
            _tg_send(regent_profile, f"【default 回复 R{r}】\n{default_resp[:500]}", tg)

        logger.info(f"Round {r} done [{sem}/{reason}] {dur:.1f}s")

    result.semantic_status = (
        "succeeded" if all(
            rr.semantic_status == "succeeded" for rr in result.rounds
        ) else "degraded" if result.rounds else "failed"
    )
    logger.info(f"ROLEPLAY end: {len(result.rounds)}/{rounds} rounds, "
                f"status={result.semantic_status}, errors={len(result.errors)}")
    return result


# ── Mode: SYNTHESIZE ────────────────────────────────────────────────
def synthesize_discuss(
    topic: str,
    target: Optional[str] = None,
    custom_prompt: Optional[str] = None,
) -> DiscussionResult:
    """
    Synthesize regent + default perspectives into unified report.

    Flow:
      1. regent sends deep analysis request to default via A2A
      2. default analyzes, posts to TG, returns full text
      3. regent receives default's analysis
      4. regent produces unified synthesis (in agent's own turn)

    Args:
        topic: Analysis topic
        target: Telegram channel ID
        custom_prompt: Override default synthesis prompt

    Returns:
        DiscussionResult with default_analysis
    """
    tg = target or DEFAULT_TG_CHANNEL
    result = DiscussionResult(mode=Mode.SYNTHESIZE, topic=topic)

    logger.info(f"SYNTHESIZE start: topic='{topic[:60]}'")

    # 1. Send to default
    a2a_prompt = custom_prompt or (
        f"{_SYNTHESIZE_PROMPT}\n\n"
        f"议题：{topic}\n\n"
        f"请做全面深度分析：背景、多角度观点、利弊权衡、建议方案。"
        f"分析完成后用 send_message 发到 Telegram 群 {tg}，"
        f"并同时返回完整文本。不要问我任何问题。"
    )
    try:
        tid = _a2a_send(a2a_prompt)
    except ConnectionError as e:
        result.errors.append(str(e))
        return result

    # 2. Wait for default
    a2a_result = _a2a_poll(tid)
    art = a2a_result.get("artifact", {})
    default_analysis = art.get("fallback_text", "") or art.get("response", "")
    sem = a2a_result.get("semantic_status", "?")
    dur = art.get("duration_s", 0)

    result.default_analysis = default_analysis
    result.semantic_status = sem
    result.duration_s = dur

    logger.info(f"SYNTHESIZE end: [{sem}] {dur:.1f}s, "
                f"analysis={len(default_analysis)} chars")

    return result


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [discuss] %(levelname)s: %(message)s",
    )

    ap = argparse.ArgumentParser(description="A2A Discussion Orchestrator")
    ap.add_argument("mode", choices=[Mode.ROLEPLAY, Mode.SYNTHESIZE],
                    help="Discussion mode")
    ap.add_argument("topic", help="Discussion topic")
    ap.add_argument("--rounds", type=int, default=3,
                    help="Number of rounds (roleplay only)")
    ap.add_argument("--target", default=None,
                    help="Telegram channel ID")
    ap.add_argument("--profile", default="regent",
                    help="Regent profile name")
    args = ap.parse_args()

    if args.mode == Mode.ROLEPLAY:
        r = roleplay_discuss(
            args.topic, rounds=args.rounds,
            regent_profile=args.profile, target=args.target,
        )
        print(f"\n{'='*60}")
        print(f"ROLEPLAY: {r.topic}")
        for rr in r.rounds:
            print(f"  R{rr.round_num} [{rr.semantic_status}/{rr.completion_reason}] "
                  f"{rr.duration_s:.0f}s")
        print(f"Status: {r.semantic_status}  Errors: {len(r.errors)}")
        print(f"{'='*60}")
        if r.errors:
            sys.exit(1)
    else:
        r = synthesize_discuss(args.topic, target=args.target)
        print(f"\n{'='*60}")
        print(f"SYNTHESIZE: {r.topic}")
        print(f"Status: {r.semantic_status}  Duration: {r.duration_s:.0f}s")
        print(f"Analysis: {len(r.default_analysis)} chars")
        print(f"{'='*60}")
        if r.errors:
            sys.exit(1)
