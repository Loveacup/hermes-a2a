"""API Server 端口分配（与 A2A 完全隔离）.

公式:
    api_server_port(profile) = 8400 + sha256("api:" + profile) % 100

设计:
- salt "api:" 用于在 100 槽内为 16 production profile 达成零碰撞
- 范围 8400-8499 完全位于 A2A 范围 (8650-8949) 之下
- sha256 跨 launchd 重启稳定 (与 A2A 的 PORT_RANGE=300 同思路)
- 公开常量便于 port-map.md / doctor.sh / 单测引用

A2A 公式（仅 docs，由 server.py 落地）:
    a2a_port(profile) = 8650 + sha256(profile) % 300
"""
from __future__ import annotations

import hashlib

API_SERVER_BASE = 8400
API_SERVER_RANGE = 100
_API_SERVER_SALT = "api:"
A2A_RANGE = (8650, 8949)


def api_server_port(profile: str) -> int:
    if not profile:
        raise ValueError("profile must be a non-empty string")
    salted = f"{_API_SERVER_SALT}{profile}".encode("utf-8")
    h = int(hashlib.sha256(salted).hexdigest(), 16)
    return API_SERVER_BASE + h % API_SERVER_RANGE
