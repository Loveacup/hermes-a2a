#!/usr/bin/env python3
"""
A2A Discussion Orchestrator — multi-round, multi-profile discussions.

Two modes:
  ROLEPLAY   — regent + default post independently to Telegram 内阁群,
               creating visible back-and-forth (since Telegram bots
               cannot see each other's messages).
  SYNTHESIZE — regent sends deep question to default via A2A,
               default responds, regent synthesizes unified report.

Usage (from regent):
  python core/discuss.py roleplay "Should we do dispatcher or audits first?"
  python core/discuss.py synthesize "Analyze EmpireThread design risks"
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

# ── Config ──────────────────────────────────────────────────────────
DEFAULT_A2A_URL = "http://127.0.0.1:8945/a2a/tasks"
DEFAULT_TG_CHANNEL = "-5133970461"  # 内阁群
HERMES_BIN = os.path.expanduser(
    "~/.hermes/hermes-agent/venv/bin/hermes"
)
A2A_URL = os.environ.get("A2A_DISCUSS_URL", DEFAULT_A2A_URL)
A2A_TIMEOUT = 300


# ── Helpers ─────────────────────────────────────────────────────────
def _a2a_send(prompt: str, context_id: Optional[str] = None) -> str:
    """Send task to default A2A and return task ID."""
    tid = f"discuss-{int(time.time())}"
    body = json.dumps({
        "id": tid,
        "context_id": context_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": prompt}],
        },
    }).encode()
    req = urllib.request.Request(
        A2A_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(req, timeout=10)
    return tid


def _a2a_poll(tid: str, max_wait: int = A2A_TIMEOUT, verbose: bool = False) -> dict:
    """Poll A2A task until completion. Returns full task dict."""
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(2)
        try:
            resp = urllib.request.urlopen(f"{A2A_URL}/{tid}", timeout=5)
            d = json.loads(resp.read())
            if d.get("status") in ("completed", "failed"):
                return d
            if verbose and int(time.time() - start) % 10 < 3:
                print(f"  ... polling {tid} ({int(time.time()-start)}s)", file=sys.stderr)
        except Exception:
            continue
    return {"status": "failed", "error": f"Timeout after {max_wait}s"}


def _tg_send(profile: str, text: str, target: Optional[str] = None) -> bool:
    """Post message to Telegram via hermes send CLI."""
    tg = target or DEFAULT_TG_CHANNEL
    cmd = [HERMES_BIN, "-p", profile, "send", "-t", f"telegram:{tg}", text]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    ok = r.returncode == 0 and "sent" in (r.stdout + r.stderr).lower()
    if not ok:
        print(f"  ⚠ tg_send failed: {r.stderr[:200]}", file=sys.stderr)
    return ok


# ── Discussion prompts ──────────────────────────────────────────────
_ROLEPLAY_SYSTEM = (
    "你是三省六部体系的主频道助手（default profile）。"
    "你正在与监国太子（regent）在 Telegram 内阁群进行一场工作讨论。"
    "太子代表三省六部全体发言，你作为主频道助手提供独立视角。"
    "每次收到太子的发言后，请直接回应——不要问问题，不要要求澄清，"
    "直接给出你的分析、建议或补充。讨论风格：专业、直接、有建设性。"
    "用 send_message 把回复发到 Telegram 群 -5133970461，"
    "落款【default】。"
)

_SYNTHESIZE_SYSTEM = (
    "你是三省六部体系的主频道助手（default profile）。"
    "监国太子请你对一个议题做深度分析。"
    "请全面分析：背景、选项、利弊、建议。"
    "最后用 send_message 发到 Telegram 群 -5133970461。"
    "同时直接返回你的完整分析文本（不通过 send_message 的部分也要有）。"
)


# ── Mode: ROLEPLAY ──────────────────────────────────────────────────
def roleplay_discuss(
    topic: str,
    rounds: int = 3,
    regent_profile: str = "regent",
    target: Optional[str] = None,
) -> dict:
    """
    Multi-round roleplay discussion between regent and default.
    Both post to Telegram 内阁群 independently, creating visible discussion.

    Flow per round:
      1. regent posts its take to TG
      2. regent sends A2A task to default (with discussion history)
      3. default responds → posts to TG
      4. regent reads default's response → formulates next round
    """
    tg = target or DEFAULT_TG_CHANNEL
    log = []
    history = []

    print(f"\n{'='*60}")
    print(f"🎭 ROLEPLAY 讨论模式：{topic[:60]}")
    print(f"{'='*60}\n")

    for r in range(1, rounds + 1):
        print(f"── 第 {r}/{rounds} 轮 ──")

        # 1. regent formulates & posts
        if r == 1:
            regent_msg = f"【太子 · 三省六部】\n议题：{topic}\n\n开个讨论。我先说我的初步判断——"
        else:
            prev_default = log[-1].get("default_response", "")
            regent_msg = (
                f"【太子 · 三省六部】\n"
                f"收到。关于你上一轮提到的 '{prev_default[:80]}...'，"
                f"我的看法是——"
            )

        # regent posts to TG
        _tg_send(regent_profile, regent_msg, tg)
        print(f"  📤 regent → TG")
        history.append(f"[regent] {regent_msg}")

        # 2. Send A2A to default
        a2a_prompt = (
            f"{_ROLEPLAY_SYSTEM}\n\n"
            f"=== 讨论历史 ===\n"
            + "\n".join(history[-6:])  # last 6 messages for context
            + f"\n\n=== 太子最新发言 ===\n{regent_msg}\n\n"
            f"请回应太子的发言。直接说你的观点，不要提问。落款【default】。"
        )
        tid = _a2a_send(a2a_prompt, context_id=f"roleplay-{topic[:30]}")
        print(f"  📤 A2A → default ({tid})")

        # 3. Wait for default's response
        result = _a2a_poll(tid, verbose=True)
        art = result.get("artifact", {})
        default_resp = art.get("fallback_text", "") or art.get("response", "")
        sem = result.get("semantic_status", "?")
        reason = result.get("completion_reason", "?")
        dur = art.get("duration_s", "?")
        print(f"  📥 default ← [{sem}/{reason}] {dur}s")

        log.append({
            "round": r,
            "regent_msg": regent_msg[:200],
            "default_response": default_resp[:300],
            "semantic_status": sem,
        })

        # If default degraded (can't send to TG), regent forwards
        if sem == "degraded":
            print(f"  ⚠ default degraded — regent 代为转发")
            _tg_send(regent_profile, f"【default 回复】\n{default_resp[:500]}", tg)

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ ROLEPLAY 完成 — {rounds} 轮")
    for entry in log:
        print(f"  第{entry['round']}轮 [{entry['semantic_status']}] "
              f"regent: {entry['regent_msg'][:60]}...")
    print(f"{'='*60}\n")
    return {"mode": "roleplay", "rounds": rounds, "log": log}


# ── Mode: SYNTHESIZE ────────────────────────────────────────────────
def synthesize_discuss(
    topic: str,
    target: Optional[str] = None,
) -> dict:
    """
    Synthesize regent + default perspectives into unified report.
    regent sends deep question to default via A2A, default responds,
    regent reads response and produces unified synthesis.

    Flow:
      1. regent sends analysis request to default via A2A
      2. default analyzes + posts to TG + returns text
      3. regent reads default's response
      4. regent produces unified synthesis
    """
    tg = target or DEFAULT_TG_CHANNEL

    print(f"\n{'='*60}")
    print(f"📋 SYNTHESIZE 讨论模式：{topic[:60]}")
    print(f"{'='*60}\n")

    # 1. Send to default
    print("── 发送分析请求 → default ──")
    a2a_prompt = (
        f"{_SYNTHESIZE_SYSTEM}\n\n"
        f"议题：{topic}\n\n"
        f"请做全面深度分析：背景、多角度观点、利弊权衡、建议方案。"
        f"分析完成后用 send_message 发到 Telegram 群 {tg}，"
        f"并同时返回完整文本。不要问我任何问题。"
    )
    tid = _a2a_send(a2a_prompt)
    print(f"  📤 A2A → default ({tid})")

    # 2. Wait for default
    print("── 等待 default 分析 ──")
    result = _a2a_poll(tid, verbose=True)
    art = result.get("artifact", {})
    default_analysis = art.get("fallback_text", "") or art.get("response", "")
    sem = result.get("semantic_status", "?")
    dur = art.get("duration_s", "?")

    print(f"  📥 default 分析完成 [{sem}] {dur}s")
    print(f"  📝 default 分析 ({len(default_analysis)} chars):")
    for line in default_analysis[:400].split("\n"):
        print(f"     │ {line[:100]}")

    # 3. regent synthesizes
    print(f"\n── regent 综合研判 ──")
    synthesis = (
        f"【三省六部 · 综合研判】\n\n"
        f"议题：{topic}\n\n"
        f"── default 分析要点 ──\n"
        f"{default_analysis[:800]}\n\n"
        f"── 太子补充 ──\n"
        f"（基于以上分析，太子后续会根据具体执行条件给出决策意见。"
        f"以上为 default 独立分析，供内阁参考。）\n"
    )

    # 4. Post synthesis to TG
    _tg_send("regent", synthesis, tg)
    print(f"  📤 综合报告 → TG")

    print(f"\n{'='*60}")
    print(f"✅ SYNTHESIZE 完成")
    print(f"{'='*60}\n")

    return {
        "mode": "synthesize",
        "default_analysis": default_analysis[:1000],
        "semantic_status": sem,
        "duration_s": dur,
    }


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="A2A Discussion Orchestrator")
    ap.add_argument("mode", choices=["roleplay", "synthesize"],
                    help="Discussion mode")
    ap.add_argument("topic", help="Discussion topic")
    ap.add_argument("--rounds", type=int, default=3,
                    help="Number of rounds (roleplay only)")
    ap.add_argument("--target", default=None,
                    help="Telegram channel ID")
    ap.add_argument("--profile", default="regent",
                    help="Regent profile name")
    args = ap.parse_args()

    if args.mode == "roleplay":
        roleplay_discuss(args.topic, rounds=args.rounds,
                        regent_profile=args.profile, target=args.target)
    else:
        synthesize_discuss(args.topic, target=args.target)
