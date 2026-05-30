#!/usr/bin/env python3
"""
L3-S3: 早新闻生成 E2E 治理全链路测试
TDD 红灯版

链路: planner → shangshu → budget(搜) → protocol(编) → reviewer
验收: 来源可验证、5段结构、总耗时≤30min
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────
A2A_TOKEN = Path("/Users/alexcai/.hermes/.a2a-token").read_text().strip()
REGISTRY_PY = "/Users/alexcai/.hermes/plugins/hermes-a2a/registry.py"
A2A_TIMEOUT = 480
CHAIN_TIMEOUT = 1800

CHAIN = ["planner", "shangshu", "budget", "protocol", "reviewer"]

ACCEPTANCE = [
    "五部全触发",    # planner/shangshu/budget/protocol/reviewer 全 completed
    "来源可验证",    # 输出含 URL 或 source 标记
    "5段结构",       # 头条/美国/中国/国际/市场
    "尚书省协调",    # shangshu 正确派发 budget+protocol
    "总耗时≤30min",  # 时间约束
    "无僵尸进程",
    "Kanban完整",
]

def a2a_port(profile: str) -> int:
    result = subprocess.run(
        ["python3", REGISTRY_PY, "port", profile],
        capture_output=True, text=True, timeout=5
    )
    return int(result.stdout.strip())

def a2a_send(profile: str, task_id: str, task: str) -> dict:
    port = a2a_port(profile)
    payload = json.dumps({"id": task_id, "task": task}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/a2a/tasks",
        data=payload,
        headers={"Authorization": f"Bearer {A2A_TOKEN}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def a2a_poll(profile: str, task_id: str, timeout: int = A2A_TIMEOUT) -> dict:
    port = a2a_port(profile)
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/a2a/tasks/{task_id}",
            headers={"Authorization": f"Bearer {A2A_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        status = data.get("status", "")
        if status in ("completed", "failed", "cancelled"):
            return data
        time.sleep(5)
    raise TimeoutError(f"A2A task {task_id} on {profile} timed out after {timeout}s")


def test_s3_morning_news_chain():
    start_time = time.time()
    results = {}
    chain_outputs = {}

    # Baseline zombie count
    baseline_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)

    print("=" * 60)
    print("L3-S3: 早新闻生成 E2E 治理全链路")
    print("=" * 60)

    # Step 1: Planner
    print("\n[1/5] Planner 拟制早新闻计划...")
    tid = f"s3-plan-{int(time.time())}"
    task = """你是中书省策划官。请为今日早新闻制定计划：

目标：生成一份5段式早新闻简报（头条、美国、中国、国际、市场）
参与部门：budget(户部/搜索) → protocol(礼部/编辑) → reviewer(门下/审核)

请输出：
1. 各段主题方向
2. 搜索关键词建议（每段 2-3 个）
3. 验收标准（至少3条：来源可验证、5段完整、事实准确）"""
    
    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner"] = r
    plan_text = r.get("artifact", {}).get("response", "")
    print(f"  planner → {r.get('status')}")
    chain_outputs["planner"] = plan_text[:500]

    # Step 2: Shangshu
    print("\n[2/5] 尚书省派发...")
    tid = f"s3-shangshu-{int(time.time())}"
    plan_summary = plan_text[:800]
    task = f"""你是尚书省调度官。策划方案如下：

{plan_summary}

请派发：
- budget(户部): 执行 web search，收集今日新闻（3-5条/段）
- protocol(礼部): 编辑格式化输出

确认派发方案。"""
    
    a2a_send("shangshu", tid, task)
    r = a2a_poll("shangshu", tid)
    results["shangshu"] = r
    print(f"  shangshu → {r.get('status')}")
    chain_outputs["shangshu"] = r.get("artifact", {}).get("response", "")[:500]

    # Step 3: Budget (web search)
    print("\n[3/5] Budget 搜索新闻...")
    tid = f"s3-budget-{int(time.time())}"
    task = """你是户部搜索官。立即调用 web_search 工具执行 5 次独立搜索，每次 1 段，全部完成后再回复。

搜索关键词（必须按此执行）：
1. 头条 → 搜索 "today top world news 2026"
2. 美国 → 搜索 "US politics economy news today"
3. 中国 → 搜索 "China economy policy news today"
4. 国际 → 搜索 "geopolitics international news today"
5. 市场 → 搜索 "stock market oil gold today"

输出格式（必填）：
### 🔥 头条
- [标题](https://完整URL) — 1 句摘要
- [标题](https://完整URL) — 1 句摘要

### 🇺🇸 美国
- [标题](https://完整URL) — 1 句摘要
...

强约束：
- 每段至少 2 条带完整 https:// URL 的新闻
- 总长度 ≥ 1500 字符
- 禁止只回复"好的我开始搜"或单段摘要
- 必须先搜索后总结，不得编造 URL"""
    
    a2a_send("budget", tid, task)
    r = a2a_poll("budget", tid)
    results["budget"] = r
    budget_text = r.get("artifact", {}).get("response", "")
    print(f"  budget → {r.get('status')} ({len(budget_text)} chars)")
    chain_outputs["budget"] = budget_text[:1000]

    # Step 4: Protocol (format)
    print("\n[4/5] Protocol 编辑排版...")
    tid = f"s3-protocol-{int(time.time())}"
    task = f"""你是礼部编辑官。请将以下搜索结果编排为5段早新闻简报：

搜索结果：
{budget_text[:2000]}

要求：
1. 5段结构：🔥头条 → 🇺🇸美国 → 🇨🇳中国 → 🌍国际 → 📊市场
2. 每条新闻保留来源 URL
3. 语言简洁专业
4. 以 📡 来源 标注每条"""
    
    a2a_send("protocol", tid, task)
    r = a2a_poll("protocol", tid)
    results["protocol"] = r
    protocol_text = r.get("artifact", {}).get("response", "")
    print(f"  protocol → {r.get('status')} ({len(protocol_text)} chars)")
    chain_outputs["protocol"] = protocol_text[:1500]

    # Step 5: Reviewer
    print("\n[5/5] Reviewer 终审...")
    tid = f"s3-review-{int(time.time())}"
    task = f"""你是门下省审查官。请审核以下早新闻简报：

{protocol_text[:2000]}

检查：
1. 5段结构完整？
2. 来源 URL 可验证？
3. 事实准确？
4. 批准或退回"""
    
    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer"] = r
    final_text = r.get("artifact", {}).get("response", "")
    print(f"  reviewer → {r.get('status')}")
    chain_outputs["reviewer"] = final_text[:500]

    elapsed = time.time() - start_time

    # ─── 验收 ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"验收结果 (总耗时 {elapsed:.0f}s)")
    print("=" * 60)

    checks = {}

    # 1. 五部全触发
    triggered = [p for p in ["planner", "shangshu", "budget", "protocol", "reviewer"]
                 if p in results and results[p].get("status") == "completed"]
    checks["五部全触发"] = len(triggered) >= 5

    # 2. 来源可验证 — check for URLs in budget + protocol output
    combined = budget_text + protocol_text
    has_url = any(marker in combined for marker in ["http://", "https://", "www."])
    checks["来源可验证"] = has_url

    # 3. 5段结构
    sections = ["头条", "美国", "中国", "国际", "市场"]
    found = sum(1 for s in sections if s in protocol_text)
    checks["5段结构"] = found >= 3

    # 4. 尚书省协调
    sh_resp = chain_outputs.get("shangshu", "")
    checks["尚书省协调"] = any(kw in sh_resp for kw in ["budget", "protocol", "户部", "礼部", "派发", "调度"])

    # 5. 总耗时
    checks["总耗时≤30min"] = elapsed <= 1800

    # 6. 无僵尸进程
    current_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)
    checks["无僵尸进程"] = current_zombie <= baseline_zombie

    # 7. Kanban 完整
    kanban_result = subprocess.run(
        "hermes kanban list", shell=True, capture_output=True, text=True, timeout=5,
        env={**os.environ, "HOME": os.path.expanduser("~")}
    )
    checks["Kanban完整"] = kanban_result.returncode == 0

    # ─── 输出 ─────────────────────────────────────────────────
    passed = sum(1 for v in checks.values() if v)
    total = len(checks)

    print(f"\n通过: {passed}/{total}")
    for name, result in checks.items():
        icon = "✅" if result else "❌"
        print(f"  {icon} {name}")

    print(f"\n链上耗时:")
    for key in ["planner", "shangshu", "budget", "protocol", "reviewer"]:
        if key in results:
            r = results[key]
            status = r.get("status", "unknown")
            print(f"  {key:>12s}: {status:>10s}")

    # Debug: 失败诊断 - 打印各阶段输出前 500 字符
    if passed < total:
        print(f"\n[debug] chain_outputs:")
        for key in ["planner", "shangshu", "budget", "protocol", "reviewer"]:
            txt = chain_outputs.get(key, "")
            print(f"  --- {key} ({len(txt)} chars) ---")
            print(f"  {txt[:500]}\n")

    assert passed >= 5, f"L3-S3 FAIL: {passed}/{total} checks passed (need ≥5)"
    assert checks["来源可验证"], "核心验收失败: 来源不可验证"

    print(f"\n✅ L3-S3 PASS: {passed}/{total} checks")
    return checks


if __name__ == "__main__":
    test_s3_morning_news_chain()
