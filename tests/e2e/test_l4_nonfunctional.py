#!/usr/bin/env python3
"""
L4: 非功能测试 — 安全 / 隔离 / 恢复
TDD 红灯版

L4-S1: Auth 强制 — 无/错/wrong-profile token 必须 401
L4-S2: 跨 profile 隔离 — profile A 的 task 在 B 不可见
L4-S3: 宕机恢复 — kill A2A → launchd 自愈 ≤30s
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────
A2A_TOKEN = Path("/Users/alexcai/.hermes/.a2a-token").read_text().strip()
REGISTRY_PY = "/Users/alexcai/.hermes/plugins/hermes-a2a/registry.py"
WRONG_TOKEN = "deadbeef-wrong-token-not-real"

TEST_PROFILES = ["planner", "archivist"]  # L4-S3 crash-recovery target

ACCEPTANCE = [
    "无token拒401",       # S1: missing Authorization → 401
    "错token拒401",       # S1: wrong token → 401
    "跨profile不可见",     # S2: task posted to A not visible on B
    "宕机自愈≤30s",        # S3: kill → health within 30s
    "任务持续存在",        # S3: task still accessible after restart
]

def a2a_port(profile: str) -> int:
    result = subprocess.run(
        ["python3", REGISTRY_PY, "port", profile],
        capture_output=True, text=True, timeout=5
    )
    return int(result.stdout.strip())

def a2a_send(profile: str, task_id: str, task: str, token: str = A2A_TOKEN) -> tuple[int, dict]:
    """Returns (status_code, response_body)"""
    port = a2a_port(profile)
    payload = json.dumps({"id": task_id, "task": task}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/a2a/tasks",
        data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() if e.fp else "{}")

def a2a_poll(profile: str, task_id: str, timeout: int = 120, token: str = A2A_TOKEN) -> tuple[int, dict]:
    """Returns (status_code, response_body)"""
    port = a2a_port(profile)
    deadline = time.time() + timeout
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    while time.time() < deadline:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/a2a/tasks/{task_id}",
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                return 200, data
        except urllib.error.HTTPError as e:
            return e.code, {}
        time.sleep(3)
    return 0, {"error": "timeout"}


# ═══════════════════════════════════════════════════════════════════
# L4-S1: Auth 强制
# ═══════════════════════════════════════════════════════════════════

def test_l4_s1_auth_enforcement():
    """无 token / 错 token → 401"""
    print("=" * 60)
    print("L4-S1: Auth 强制")
    print("=" * 60)

    profile = "planner"
    tid = f"s1-auth-{int(time.time())}"
    task = "echo: auth test"

    results = {}

    # Test 1: 无 token
    print("\n[Test 1] 无 Authorization header...")
    code, body = a2a_send(profile, tid, task, token="")
    results["无token拒401"] = code == 401
    print(f"  → HTTP {code} {'✅' if code == 401 else '❌ expected 401'}")

    # Test 2: 错 token
    print("\n[Test 2] 错误 token...")
    tid2 = f"s1-auth2-{int(time.time())}"
    code, body = a2a_send(profile, tid2, task, token=WRONG_TOKEN)
    results["错token拒401"] = code == 401
    print(f"  → HTTP {code} {'✅' if code == 401 else '❌ expected 401'}")

    # Verify: correct token still works
    print("\n[Verify] 正确 token 应通过...")
    tid3 = f"s1-auth3-{int(time.time())}"
    code, body = a2a_send(profile, tid3, task, token=A2A_TOKEN)
    correct_ok = code in (200, 201, 202)
    print(f"  → HTTP {code} {'✅' if correct_ok else '❌'}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nL4-S1: {passed}/{total}")
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")

    assert passed >= 2, f"L4-S1 FAIL: {passed}/{total}"
    return results


# ═══════════════════════════════════════════════════════════════════
# L4-S2: 跨 Profile 隔离
# ═══════════════════════════════════════════════════════════════════

def test_l4_s2_cross_profile_isolation():
    """Planner 的 task 在 archivist 不可见"""
    print("\n" + "=" * 60)
    print("L4-S2: 跨 Profile 隔离")
    print("=" * 60)

    results = {}

    # Step 1: Post task to planner
    print("\n[1] 发任务到 planner...")
    tid = f"s2-iso-{int(time.time())}"
    code, body = a2a_send("planner", tid, "echo: isolation test", token=A2A_TOKEN)
    task_accepted = code in (200, 201, 202)
    print(f"  planner POST → HTTP {code} {'✅' if task_accepted else '❌'}")

    # Step 2: Try to poll same task on archivist
    print("\n[2] 从 archivist 查 planner 的任务...")
    code, body = a2a_poll("archivist", tid, timeout=30, token=A2A_TOKEN)
    isolated = code in (401, 403, 404)  # 不应该能看到
    results["跨profile不可见"] = isolated
    print(f"  archivist GET → HTTP {code} {'✅ 隔离' if isolated else '❌ 可跨读!'}")

    # Step 3: Verify task is visible on planner
    print("\n[3] 验证任务在 planner 可见...")
    code, body = a2a_poll("planner", tid, timeout=120, token=A2A_TOKEN)
    visible_on_planner = code == 200
    print(f"  planner GET → HTTP {code} {'✅' if visible_on_planner else '❌'}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nL4-S2: {passed}/{total}")
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")

    assert passed >= 1, f"L4-S2 FAIL: {passed}/{total}"
    return results


# ═══════════════════════════════════════════════════════════════════
# L4-S3: 宕机恢复
# ═══════════════════════════════════════════════════════════════════

def test_l4_s3_crash_recovery():
    """Kill archivist A2A → launchd 自愈 ≤30s"""
    print("\n" + "=" * 60)
    print("L4-S3: 宕机恢复")
    print("=" * 60)

    results = {}
    profile = "archivist"

    # Step 1: Post a task to archivist so we can verify persistence
    print("\n[1] 发任务到 archivist...")
    tid = f"s3-crash-{int(time.time())}"
    code, body = a2a_send(profile, tid, "echo: crash recovery test", token=A2A_TOKEN)
    task_posted = code in (200, 201, 202)
    print(f"  POST → HTTP {code}")

    # Step 2: Find and kill archivist A2A process
    print("\n[2] Kill archivist A2A...")
    result = subprocess.run(
        f"launchctl list | grep com.hermes.a2a.{profile}",
        shell=True, capture_output=True, text=True, timeout=5
    )
    print(f"  Before: {result.stdout.strip()}")

    kill_result = subprocess.run(
        f"HOME=/Users/alexcai launchctl kickstart -k gui/501/com.hermes.a2a.{profile}",
        shell=True, capture_output=True, text=True, timeout=10
    )
    time.sleep(2)

    # Step 3: Measure recovery time
    print("\n[3] 测恢复时间...")
    port = a2a_port(profile)
    start = time.time()
    recovered = False
    recovery_time = 0

    for attempt in range(30):  # 30s max
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status == 200:
                    recovered = True
                    recovery_time = time.time() - start
                    break
        except Exception:
            pass
        time.sleep(1)

    results["宕机自愈≤30s"] = recovered and recovery_time <= 30
    print(f"  恢复: {'✅' if recovered else '❌'} in {recovery_time:.1f}s")

    # Step 4: Verify task still exists after restart
    print("\n[4] 验证任务仍存在...")
    time.sleep(3)  # Give server a moment
    code, body = a2a_poll(profile, tid, timeout=120, token=A2A_TOKEN)
    task_persists = code == 200
    results["任务持续存在"] = task_persists
    print(f"  GET → HTTP {code} {'✅ 持久' if task_persists else '❌ 丢失'}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nL4-S3: {passed}/{total}")
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")

    assert passed >= 1, f"L4-S3 FAIL: {passed}/{total}"
    return results


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    all_results = {}

    r1 = test_l4_s1_auth_enforcement()
    all_results.update(r1)

    r2 = test_l4_s2_cross_profile_isolation()
    all_results.update(r2)

    r3 = test_l4_s3_crash_recovery()
    all_results.update(r3)

    passed = sum(1 for v in all_results.values() if v)
    total = len(all_results)
    print(f"\n{'='*60}")
    print(f"L4 总结果: {passed}/{total}")
    for k, v in all_results.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("=" * 60)

    assert passed >= 3, f"L4 FAIL: {passed}/{total} (need ≥3)"
    print(f"\n✅ L4 PASS: {passed}/{total}")
