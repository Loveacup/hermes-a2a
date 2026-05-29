"""T2 RED: port_resolver.py — API Server 端口公式 + 校验.

公式: 8400 + sha256("api:" + profile) % 100
约束:
- 16 profile 零碰撞
- 与 A2A 端口空间 (8654-8945) 完全隔离
- 确定性（跨进程、跨重启稳定）
"""
from __future__ import annotations

import pytest

from port_resolver import (  # type: ignore
    api_server_port,
    API_SERVER_BASE,
    API_SERVER_RANGE,
    A2A_RANGE,
)

THE_16 = [
    "archivist", "auditor", "budget", "default", "dispatcher",
    "engineer", "gongbu", "hanlinyuan", "jiangzuojian", "planner",
    "protocol", "regent", "registry", "reviewer", "shangshu", "tester",
]


# ── R1: 公式参数 ──────────────────────────────────────────────

def test_r_r1_constants():
    assert API_SERVER_BASE == 8400
    assert API_SERVER_RANGE == 100
    # A2A 范围: 文档化的 8650-8949 (PORT_RANGE=300)
    assert A2A_RANGE == (8650, 8949)


# ── R2: 端口在声明范围内 ──────────────────────────────────────

def test_r_r2_port_in_range():
    for p in THE_16:
        port = api_server_port(p)
        assert API_SERVER_BASE <= port < API_SERVER_BASE + API_SERVER_RANGE


# ── R3: 与 A2A 隔离 ──────────────────────────────────────────

def test_r_r3_isolated_from_a2a():
    lo, hi = A2A_RANGE
    for p in THE_16:
        port = api_server_port(p)
        assert not (lo <= port <= hi), \
            f"{p} → {port} 落入 A2A 空间 {A2A_RANGE}"


# ── R4: 16 profile 零碰撞 ─────────────────────────────────────

def test_r_r4_zero_collision_16():
    ports = [api_server_port(p) for p in THE_16]
    assert len(set(ports)) == len(THE_16), \
        f"碰撞: {len(THE_16) - len(set(ports))} 处"


# ── R5: 确定性（同输入同输出） ───────────────────────────────

def test_r_r5_deterministic():
    for p in THE_16:
        assert api_server_port(p) == api_server_port(p)


# ── R6: 不同 profile 端口必然不同（强保证） ──────────────────

def test_r_r6_distinct_for_known_set():
    """对 16 个生产 profile 保证两两不同."""
    for i, a in enumerate(THE_16):
        for b in THE_16[i + 1:]:
            assert api_server_port(a) != api_server_port(b), \
                f"{a} 与 {b} 端口相同"


# ── R7: 空 profile 抛错 ───────────────────────────────────────

def test_r_r7_empty_profile_raises():
    with pytest.raises(ValueError):
        api_server_port("")
