---
project: hermes-a2a
status: Step 1 — 尚书省 API Hub
created: 2026-05-27
repo: https://github.com/finalhour/hermes-a2a
---

# hermes-a2a：三省六部 A2A 互通协议

## 概述

基于 Google A2A Protocol (agent-to-agent, 23.4K⭐) 的 Hermes 全局插件。为三省六部 15 个 profile 提供标准化的跨部门通信能力——能力发现、任务委派、进度流式传输。

## 动机

当前三省六部 profile 间通信仅靠 Kanban 卡（异步投递 + 60s 轮询），无法做同步 on-demand 查询。内阁 API 桥接（default↔regent 的 localhost HTTP）已验证同步通信模式的可行性。hermes-a2a 将此模式标准化、规模化。

## 架构

```
┌──────────────────────────────────────────────────┐
│                  hermes-a2a                        │
│                                                    │
│  Agent Card ──→ 能力清单（自动从 config 生成）      │
│  Task       ──→ 工作单元（id + status + artifact）  │
│  Stream     ──→ SSE 流式进度                        │
│                                                    │
│  Transport: HTTP/JSON (future: JSON-RPC 2.0)       │
└──────────────────────────────────────────────────┘
```

## 部署计划

### 当前状态：6-profile launchd 监管（已生产就绪）

| Profile | 端口 | 技能数 | 执行模式 |
|---------|------|--------|----------|
| engineer | 8668 | 5 | subprocess |
| shangshu | 8676 | 4 | subprocess |
| budget | 8686 | 3 | subprocess |
| regent | 8689 | 13 | API Server /v1/runs |
| default | 8695 | 13 | API Server /v1/runs |
| gongbu | 8698 | 4 | subprocess |

端口公式：`sha256(profile) % 300 + 8650`（16 profile 零碰撞验证通过）。全部由 launchd KeepAlive 监管，ThrottleInterval=30s。

### Step 1: 核心 4 profile（已完成 2026-05-27）

### Step 2: 全量部署（10+ profile，端口公式化）

### Step 3: EmpireThread 事件桥（MEMORY_QUERY → Hindsight 跨部门只读）

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/a2a/.well-known/agent-card.json` | Agent Card |
| POST | `/a2a/tasks` | 创建任务 |
| GET | `/a2a/tasks/{id}` | 任务状态 |
| GET | `/a2a/tasks/{id}/stream` | SSE 流式进度 |

## 文件清单

```
hermes-a2a/（源码 ~/code/hermes-a2a/）
├── plugin.py              # Hermes 插件加载器
├── server.py              # A2A HTTP Server
├── agent_card.py          # Agent Card 自动生成
├── task_handler.py        # Task → Hermes agent（双模执行）
├── plugin.yaml            # 插件元数据
├── requirements.txt       # pyyaml
├── scripts/
│   ├── hermes-a2a-doctor.sh   # 健康聚合器
│   └── seed-a2a-symlinks.sh   # per-profile symlink 种子
├── docs/
│   ├── tracking.md            # 项目追踪
│   ├── methodology.md         # 方法论文档 + ADR
│   ├── architecture-comparison.md  # A2A vs API Bridge 对比
│   ├── deployment-report.md       # 部署接入报告
│   └── audits/
│       ├── 01-initial-audit.md        # 首轮审计（CC agent team）
│       ├── 02-reaudit.md              # 二轮审计（3-agent team）
│       └── 02-reaudit-ops-agent.md    # Ops agent 详细报告
├── CLAUDE.md             # AI 协作文档
└── README.md             # 用户文档
```

## 关联

- A2A Protocol: https://github.com/a2aproject/A2A
- 三省六部宪章: `three-provinces-constitution` skill
- EmpireThread P4: capability_snapshot + heartbeat + MEMORY_QUERY
- 内阁 API 桥接: default↔regent localhost HTTP

## 日志

- 2026-05-27: 项目立项。创建 GitHub 仓库 + Obsidian 文档。
- 2026-05-27: Step 1 核心 4 profile 部署 + regent/default API Server 接入。
- 2026-05-27: 首轮 CC agent team 审计 — 发现 P0×3（端口碰撞/task_handler 未接通/无看门狗）。
- 2026-05-27: P0/P1/P2 修复（PORT_RANGE 50→200, threading 接通, launchd 全量监管, SKILL_MAP A2A 1.0, 双模执行, seed symlink 脚本）。
- 2026-05-27: 二轮 CC 3-agent team 审计 — 确认 P0-2/P0-3 已修, 发现 NEW-P0-A（源码未同步）+ NEW-P0-B（PORT_RANGE=200 仍有碰撞）。
- 2026-05-27: NEW-P0-A/B 修复 — PORT_RANGE 200→300（零碰撞）, 源码 ↔ 部署 ↔ GitHub 三同步, commit `d4b73a4`。v0.1.1 生产就绪。
