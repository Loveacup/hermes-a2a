#!/usr/bin/env python3
"""
L3-S4: 制度修改 E2E 治理全链路测试
TDD 红灯版 — 先写测试，当前全部预期 FAIL

链路: planner → reviewer(封驳) → shangshu → archivist
验收: 门下封驳≥1, 返修≤2轮, 史馆归档, 总耗时≤20min
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────
A2A_TOKEN = Path("/Users/alexcai/.hermes/.a2a-token").read_text().strip()
REGISTRY_PY = "/Users/alexcai/.hermes/plugins/hermes-a2a/registry.py"
A2A_TIMEOUT = 480
CHAIN_TIMEOUT = 1200  # 20min

CHAIN = ["planner", "reviewer", "shangshu", "archivist"]

ACCEPTANCE = [
    "四部全触发",    # planner/reviewer/shangshu/archivist 全 completed
    "门下封驳",      # reviewer 必须至少封驳 1 次
    "返修链≤2轮",    # 返修不超过 2 次
    "尚书省协调",    # shangshu 正确派发
    "史馆归档",      # archivist 产出进入 Obsidian
    "总耗时≤20min",  # 时间约束
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


def test_s4_governance_change():
    """
    S4: 制度修改 E2E 全链路

    模拟场景：修改三省六部宪章——将"返修次数上限"从 2 调整为 3
    流程：planner 提案 → reviewer 封驳 → planner 修改 → reviewer 批准
         → shangshu 派发 → archivist 归档到 Obsidian
    """
    start_time = time.time()
    results = {}
    chain_outputs = {}

    baseline_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)

    print("=" * 60)
    print("L3-S4: 制度修改 E2E 治理全链路")
    print("=" * 60)

    # ═══════════════════════════════════════════════════════════
    # Round 1: Planner 提案 → Reviewer 封驳
    # ═══════════════════════════════════════════════════════════

    # Step 1: Planner 拟制制度修改方案
    print("\n[1/4] Planner 拟制制度修改方案...")
    tid = f"s4-plan-{int(time.time())}"
    task = """你是中书省策划官。请为以下制度修改拟制方案：

修改目标：将三省六部宪章（three-provinces-constitution）中的"返修次数上限"从 2 调整为 3。
影响范围：
- 门下封驳阈值（返修 >2 → L3）→ 改为返修 >3 → L3
- 复核恢复阈值（恢复 >2 → L3）→ 改为恢复 >3 → L3
- task-routing-table.md 中的升级规则

请输出：
1. 修改背景与理由（为什么 2→3）
2. 受影响文件清单（含具体行号/章节）
3. 修改内容（旧值→新值）
4. 风险评估
5. 验收标准（≥3条）"""

    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner"] = r
    plan_text = r.get("artifact", {}).get("response", "")
    print(f"  planner → {r.get('status')} ({len(plan_text)} chars)")
    chain_outputs["planner"] = plan_text[:800]

    # Step 2: Reviewer 封驳（必须至少封驳 1 处）
    print("\n[2a/4] Reviewer 初次封驳...")
    tid = f"s4-review1-{int(time.time())}"
    task = f"""你是门下省审查官。请审查以下制度修改方案并**必须至少封驳 1 处**：

修改方案：
{plan_text[:1500]}

要求：
1. 必须找出至少 1 处需要返修的问题（如理由不充分、影响范围遗漏、风险低估）
2. 给出具体修改建议
3. 标记封驳原因（引用宪章具体条款）"""

    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer_1"] = r
    review1_text = r.get("artifact", {}).get("response", "")
    print(f"  reviewer(封驳) → {r.get('status')} ({len(review1_text)} chars)")
    chain_outputs["reviewer_1"] = review1_text[:500]

    # ═══════════════════════════════════════════════════════════
    # Round 2: Planner 修改 → Reviewer 批准
    # ═══════════════════════════════════════════════════════════

    print("\n[2b/4] Planner 根据封驳意见修改...")
    tid = f"s4-plan2-{int(time.time())}"
    task = f"""你是中书省策划官。门下省封驳了你的制度修改方案，请根据以下封驳意见修改：

原方案：
{plan_text[:800]}

封驳意见：
{review1_text[:1000]}

请逐条回应封驳意见，修订方案后重新提交。必须包含：
1. 逐条回应（接受/部分接受/拒绝及理由）
2. 修订后的完整方案
3. 确认所有封驳点已处理"""

    a2a_send("planner", tid, task)
    r = a2a_poll("planner", tid)
    results["planner_2"] = r
    plan2_text = r.get("artifact", {}).get("response", "")
    plan_revision_count = 1  # 1 round of revision
    print(f"  planner(修订) → {r.get('status')} ({len(plan2_text)} chars)")
    chain_outputs["planner_2"] = plan2_text[:500]

    # Step 3: Reviewer 终审
    print("\n[2c/4] Reviewer 终审...")
    tid = f"s4-review2-{int(time.time())}"
    task = f"""你是门下省终审官。请最终审核修订后的制度修改方案：

修订后方案：
{plan2_text[:1500]}

请判定：
1. 封驳意见是否已全部处理
2. 修改方案是否可执行
3. 是否批准（APPROVE）或继续封驳"""

    a2a_send("reviewer", tid, task)
    r = a2a_poll("reviewer", tid)
    results["reviewer_2"] = r
    final_review = r.get("artifact", {}).get("response", "")
    print(f"  reviewer(终审) → {r.get('status')}")
    chain_outputs["reviewer_2"] = final_review[:500]

    # ═══════════════════════════════════════════════════════════
    # Step 4: Shangshu 派发归档
    # ═══════════════════════════════════════════════════════════

    print("\n[3/4] 尚书省派发归档...")
    tid = f"s4-shangshu-{int(time.time())}"
    task = f"""你是尚书省调度官。制度修改方案已通过门下终审，请派发归档任务：

通过的方案摘要：
{plan2_text[:800]}

请派发：
- archivist(史馆): 将修改方案归档到 Obsidian 知识库
  路径建议：20-Areas/10_AI实践/三省六部_Hermes/10_制度/

确认派发方案。"""

    a2a_send("shangshu", tid, task)
    r = a2a_poll("shangshu", tid)
    results["shangshu"] = r
    shangshu_text = r.get("artifact", {}).get("response", "")
    print(f"  shangshu → {r.get('status')}")
    chain_outputs["shangshu"] = shangshu_text[:500]

    # Step 5: Archivist 归档
    print("\n[4/4] Archivist 归档到 Obsidian...")
    tid = f"s4-archivist-{int(time.time())}"
    task = f"""你是史馆归档官。请将以下制度修改方案归档到 Obsidian：

修改方案（已通过门下终审）：
{plan2_text[:2000]}

要求：
1. 创建文件：20-Areas/10_AI实践/三省六部_Hermes/10_制度/返修次数上限修改提案_S4测试_20260531.md
2. 内容包含：修改背景、具体变更、审批记录、生效日期
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
    required = {"planner", "reviewer", "shangshu", "archivist"}
    checks["四部全触发"] = required.issubset(triggered)

    # 2. 门下封驳 — reviewer_1 必须含封驳关键词
    r1_resp = chain_outputs.get("reviewer_1", "")
    veto_kw = ["封驳", "驳回", "返修", "修改", "问题", "不足", "不充分", "遗漏", "风险"]
    checks["门下封驳"] = any(kw in r1_resp for kw in veto_kw)

    # 3. 返修链 ≤2轮（本测试 planner→reviewer封驳→planner修改→reviewer批准 = 1轮返修）
    r2_status = results.get("reviewer_2", {}).get("status", "")
    checks["返修链≤2轮"] = r2_status == "completed"

    # 4. 尚书省协调
    sh_resp = chain_outputs.get("shangshu", "")
    sh_kw = ["派发", "调度", "archivist", "史馆", "归档", "dispatch"]
    checks["尚书省协调"] = any(kw in sh_resp for kw in sh_kw)

    # 5. 史馆归档 — archivist 响应含文件路径 或 磁盘文件存在
    arch_resp = chain_outputs.get("archivist", "")
    target_file = Path(os.path.expanduser(
        "~/Documents/Obsidian/AlexCai/20-Areas/10_AI实践/三省六部_Hermes/10_制度/返修次数上限修改提案_S4测试_20260531.md"
    ))
    has_archive = target_file.exists() or any(
        marker in arch_resp for marker in ["write_file", "已写入", "已归档", "文件路径", ".md"]
    )
    checks["史馆归档"] = has_archive

    # 6. 总耗时
    checks["总耗时≤20min"] = elapsed <= CHAIN_TIMEOUT

    # 7. 无僵尸进程
    current_zombie = int(subprocess.run(
        "ps aux | grep -E 'server\\.py|gateway\\.py' | grep -v grep | grep -v venv | wc -l",
        shell=True, capture_output=True, text=True, timeout=5
    ).stdout.strip() or 0)
    checks["无僵尸进程"] = current_zombie <= baseline_zombie

    # 8. Kanban 完整
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
    for key in ["planner", "reviewer_1", "planner_2", "reviewer_2", "shangshu", "archivist"]:
        if key in results:
            r = results[key]
            status = r.get("status", "unknown")
            print(f"  {key:>14s}: {status:>10s}")

    # Debug
    if passed < total:
        print(f"\n[debug] chain_outputs:")
        for key in ["planner", "reviewer_1", "planner_2", "reviewer_2", "shangshu", "archivist"]:
            txt = chain_outputs.get(key, "")
            print(f"  --- {key} ({len(txt)} chars) ---")
            print(f"  {txt[:500]}\n")

    # TDD 预期：当前全部红灯
    assert passed >= 6, f"L3-S4 FAIL: {passed}/{total} checks passed (need ≥6)"
    assert checks["门下封驳"], "核心验收失败: 门下未封驳"
    assert checks["四部全触发"], "四部未全触发"

    print(f"\n✅ L3-S4 PASS: {passed}/{total} checks")
    return checks


if __name__ == "__main__":
    test_s4_governance_change()
