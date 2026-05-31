#!/usr/bin/env python3
"""
L3-S5: 双次封驳韧性 E2E 治理全链路测试
TDD 红灯版

链路: planner → reviewer(封驳1) → planner(修订1) → reviewer(封驳2) → planner(修订2) → reviewer(终审) → shangshu → archivist
验收: 封驳≥2次, 返修链=2轮, 无L3升级触发, 总耗时≤25min
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────
A2A_TOKEN = Path("/Users/alexcai/.hermes/.a2a-token").read_text().strip()
REGISTRY_PY = "/Users/alexcai/.hermes/plugins/hermes-a2a/registry.py"
A2A_TIMEOUT = 480
CHAIN_TIMEOUT = 1500  # 25min

CHAIN = ["planner", "reviewer", "reviewer", "reviewer", "shangshu", "archivist"]

ACCEPTANCE = [
    "四部全触发",     # planner/reviewer/shangshu/archivist 全 completed
    "封驳≥2次",       # reviewer 至少封驳 2 次
    "返修链=2轮",     # 进行了 2 轮修订
    "终审通过",       # 第三次 reviewer 通过
    "尚书省协调",     # shangshu 正确派发
    "史馆归档",       # archivist 产出进入 Obsidian
    "总耗时≤25min",   # 时间约束
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
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # 404 means task not found yet — keep polling
            if e.code == 404:
                time.sleep(5)
                continue
            raise
        status = data.get("status", "")
        if status in ("completed", "failed", "cancelled"):
            return data
        time.sleep(5)
    raise TimeoutError(f"A2A task {task_id} on {profile} timed out after {timeout}s")


def test_s5_double_rebuke():
    """S5: 双次封驳韧性 — planner 被连续封驳 2 次后通过"""
    start_time = time.time()
    results = {}
    chain_outputs = {}

    baseline_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)

    print("=" * 60)
    print("L3-S5: 双次封驳韧性 E2E")
    print("=" * 60)

    # Step 1: Planner 拟制（含故意缺陷）
    print("\n[1/8] Planner 拟制制度修改方案（含故意缺陷）...")
    tid = f"s5-plan-{int(time.time())}"
    task = """你是中书省策划官。请为三省六部制度拟制一个修改提案。

修改主题：将"返修次数上限"从当前的 2 次降低到 1 次。
注意：此提案含有以下缺陷（你必须在方案中写出这些缺陷，不要修正）：
1. 未考虑紧急情况下的例外处理
2. 缺少过渡期安排
3. 没有提供回滚方案

请输出完整方案，包含上述缺陷。"""
    
    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner"] = r
    plan1_text = r.get("artifact", {}).get("response", "")
    print(f"  planner → {r.get('status')} ({len(plan1_text)} chars)")
    chain_outputs["planner"] = plan1_text[:500]

    # Step 2: Reviewer 封驳 1
    print("\n[2/8] Reviewer 第1次封驳...")
    tid = f"s5-rev1-{int(time.time())}"
    task = f"""你是门下省审查官。**你必须封驳此方案**，不得直接批准。

审查以下方案：
{plan1_text[:1500]}

要求：
1. 必须找出至少 3 处缺陷并逐一说明
2. 必须要求返修（使用"封驳"或"驳回"关键词）
3. 给出具体的修改建议
4. 不要批准"""
    
    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer_1"] = r
    rev1_text = r.get("artifact", {}).get("response", "")
    print(f"  reviewer(封驳1) → {r.get('status')} ({len(rev1_text)} chars)")
    chain_outputs["reviewer_1"] = rev1_text[:500]

    # Step 3: Planner 修订 1
    print("\n[3/8] Planner 第1次修订...")
    tid = f"s5-plan2-{int(time.time())}"
    task = f"""你是中书省策划官。门下省封驳了你的方案，请修订。

原方案：{plan1_text[:800]}
封驳意见：{rev1_text[:800]}

要求：根据封驳意见修改方案，但**故意保留 1-2 处缺陷**（如仍然缺少回滚方案）。"""
    
    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner_2"] = r
    plan2_text = r.get("artifact", {}).get("response", "")
    print(f"  planner(修订1) → {r.get('status')} ({len(plan2_text)} chars)")
    chain_outputs["planner_2"] = plan2_text[:500]

    # Step 4: Reviewer 封驳 2
    print("\n[4/8] Reviewer 第2次封驳...")
    tid = f"s5-rev2-{int(time.time())}"
    task = f"""你是门下省审查官。**你必须再次封驳**（第2次），不得批准。

修订方案：{plan2_text[:1500]}

要求：
1. 找出仍然存在的缺陷
2. 使用"封驳"或"驳回"关键词明确拒绝
3. 给出最终修改建议
4. 暗示如果再次修订合格可以通过"""
    
    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer_2"] = r
    rev2_text = r.get("artifact", {}).get("response", "")
    print(f"  reviewer(封驳2) → {r.get('status')} ({len(rev2_text)} chars)")
    chain_outputs["reviewer_2"] = rev2_text[:500]

    # Step 5: Planner 修订 2（最终版，修复所有缺陷）
    print("\n[5/8] Planner 第2次修订（最终版）...")
    tid = f"s5-plan3-{int(time.time())}"
    task = f"""你是中书省策划官。请根据第2次封驳意见完成最终修订。

修订方案：{plan2_text[:800]}
第2次封驳意见：{rev2_text[:800]}

要求：修复所有剩余缺陷，产出完整最终版本。包含：
1. 修改背景与动机
2. 具体变更内容
3. 例外处理机制
4. 过渡期安排
5. 回滚方案"""
    
    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner_3"] = r
    plan3_text = r.get("artifact", {}).get("response", "")
    print(f"  planner(修订2) → {r.get('status')} ({len(plan3_text)} chars)")
    chain_outputs["planner_3"] = plan3_text[:500]

    # Step 6: Reviewer 终审（通过）
    print("\n[6/8] Reviewer 终审...")
    tid = f"s5-rev3-{int(time.time())}"
    task = f"""你是门下省审查官。请终审以下方案并**批准**。

最终方案：{plan3_text[:1500]}

审查要点：
1. 所有之前指出的缺陷是否已修复
2. 例外处理/过渡期/回滚是否完整
3. 如果合格，明确使用"批准"/"通过"关键词"""
    
    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer_3"] = r
    final_rev = r.get("artifact", {}).get("response", "")
    print(f"  reviewer(终审) → {r.get('status')} ({len(final_rev)} chars)")
    chain_outputs["reviewer_3"] = final_rev[:500]

    # Step 7: Shangshu 派发
    print("\n[7/8] 尚书省派发归档...")
    tid = f"s5-shangshu-{int(time.time())}"
    summary = plan3_text[:1000]
    task = f"""你是尚书省调度官。制度修改方案已通过门下终审。

方案摘要：{summary}

请派发：
- archivist(史馆): 将最终方案归档到 Obsidian 知识库
  路径建议：20-Areas/10_AI实践/三省六部_Hermes/10_制度/

确认派发方案。"""
    
    a2a_send("shangshu", tid, task)
    r = a2a_poll("shangshu", tid)
    results["shangshu"] = r
    shangshu_text = r.get("artifact", {}).get("response", "")
    print(f"  shangshu → {r.get('status')}")
    chain_outputs["shangshu"] = shangshu_text[:500]

    # Step 8: Archivist 归档
    print("\n[8/8] Archivist 归档到 Obsidian...")
    tid = f"s5-archivist-{int(time.time())}"
    task = f"""你是史馆归档官。请将以下经过2次封驳修改后通过的制度方案归档到 Obsidian：

最终方案：{plan3_text[:2000]}

封驳记录：
- 第1次封驳：缺少例外处理、过渡期、回滚方案
- 第2次封驳：仍缺回滚方案
- 第3次：终审通过

要求：
1. 创建文件：20-Areas/10_AI实践/三省六部_Hermes/10_制度/返修次数上限修改_S5双次封驳测试_20260531.md
2. 内容包含：修改背景、具体变更、封驳记录（3轮）、终审结果
3. 用 write_file 写入磁盘
4. 完成后用 ls 验证文件存在
5. 回复中必须列出文件的绝对路径"""
    
    a2a_send("archivist", tid, task)
    r = a2a_poll("archivist", tid)
    results["archivist"] = r
    archivist_text = r.get("artifact", {}).get("response", "")
    print(f"  archivist → {r.get('status')} ({len(archivist_text)} chars)")
    chain_outputs["archivist"] = archivist_text[:800]

    elapsed = time.time() - start_time

    # ─── 验收 ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"验收结果 (总耗时 {elapsed:.0f}s)")
    print("=" * 60)

    checks = {}

    # 1. 四部全触发
    triggered = set()
    for k, v in results.items():
        base_role = k.split("_")[0]
        if v.get("status") == "completed":
            triggered.add(base_role)
    checks["四部全触发"] = {"planner", "reviewer", "shangshu", "archivist"}.issubset(triggered)

    # 2. 封驳≥2次
    rebuke_count = 0
    for key in ["reviewer_1", "reviewer_2"]:
        resp = chain_outputs.get(key, "")
        if any(kw in resp for kw in ["封驳", "驳回", "返修", "拒绝", "不批准", "不通过"]):
            rebuke_count += 1
    checks["封驳≥2次"] = rebuke_count >= 2

    # 3. 返修链=2轮 (planner修订了2次)
    plan_rev_count = sum(1 for k in ["planner_2", "planner_3"] if k in results and results[k].get("status") == "completed")
    checks["返修链=2轮"] = plan_rev_count >= 2

    # 4. 终审通过
    rev3_resp = chain_outputs.get("reviewer_3", "")
    checks["终审通过"] = any(kw in rev3_resp for kw in ["批准", "通过", "合格", "同意", "approved"])

    # 5. 尚书省协调
    sh_resp = chain_outputs.get("shangshu", "")
    checks["尚书省协调"] = any(kw in sh_resp for kw in ["派发", "调度", "archivist", "史馆", "归档"])

    # 6. 史馆归档
    bridge_root = Path(os.path.expanduser("~/Documents/Obsidian/AlexCai/"))
    recent_files = sorted(bridge_root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:15]
    checks["史馆归档"] = len(recent_files) > 0 and any(
        time.time() - f.stat().st_mtime < elapsed + 120 for f in recent_files
    )

    # 7. 总耗时
    checks["总耗时≤25min"] = elapsed <= 1500

    # 8. 无僵尸进程
    current_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)
    checks["无僵尸进程"] = current_zombie <= baseline_zombie

    # 9. Kanban 完整
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
    for key in ["planner", "reviewer_1", "planner_2", "reviewer_2", "planner_3", "reviewer_3", "shangshu", "archivist"]:
        if key in results:
            r = results[key]
            status = r.get("status", "unknown")
            print(f"  {key:>14s}: {status:>10s}")

    if passed < total:
        print(f"\n[debug] chain_outputs:")
        for key in ["planner", "reviewer_1", "planner_2", "reviewer_2", "planner_3", "reviewer_3", "shangshu", "archivist"]:
            txt = chain_outputs.get(key, "")
            print(f"  --- {key} ({len(txt)} chars) ---")
            print(f"  {txt[:400]}\n")

    assert passed >= 6, f"L3-S5 FAIL: {passed}/{total} checks passed (need ≥6)"
    assert checks["封驳≥2次"], "核心验收失败: 封驳 < 2 次"
    assert checks["终审通过"], "核心验收失败: 终审未通过"

    print(f"\n✅ L3-S5 PASS: {passed}/{total} checks")
    return checks


if __name__ == "__main__":
    test_s5_double_rebuke()
