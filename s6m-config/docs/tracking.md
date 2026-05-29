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

### Step 3: EmpireThread 事件桥（MEMORY_QUERY → Supermemory 跨部门只读，ADR-005 单层架构）

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
- 2026-05-28: Monorepo 拆分 — core/（通用内核）+ s6m-config/（三省六部配置）, commit `6a4ce85`。
- 2026-05-28: Obsidian 全量同步 — 11 份文档入 vault（Inbox 3 + 审计 5 + 方案 3）。
- 2026-05-28: 结果语义升级 — task_handler 增加 `semantic_status`（succeeded/degraded/failed）+ `completion_reason` + `fallback_text`。修复 A2A 任务 blind-complete + 无回退问题。
- 2026-05-28: 信号覆盖补丁 — 中文口语化表达（已发到/已发至/已发出）纳入分类器。commit `2a19515`。
- 2026-05-28: 讨论模式 — core/discuss.py 支持两种模式：ROLEPLAY（多轮角色扮演，双方各自发 TG 内阁群）和 SYNTHESIZE（default 深度分析 → regent 综合研判）。s6m-config/discuss-modes.yaml 配置。
- 2026-05-30: EventBridge sink Hindsight → Supermemory 替换（ADR-005, 决策依据 ARCH-TEST-001）。删除 `core/event_bridge/sinks/hindsight.py`（-196 行）与 `test_event_bridge_hindsight.py`（-10 用例），新建 `sinks/supermemory.py`（urllib 直发 `POST https://api.supermemory.ai/v3/documents`，camelCase payload）与 `test_event_bridge_supermemory.py`（+9 用例 S1-S9）。daemon 条件改 `SUPERMEMORY_API_KEY`，launchd plist wrap zsh 源 `~/.hermes/.env`。container_tag 映射：regent → `hermes-cabinet`，default/其他 → fallback `hermes`。当前共 52 个 event_bridge 测试全绿；部署同步完成，已观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}`。文档全面同步：methodology / EmpireThread v2 缩窄版 / 综合设计文档 v1.0 / 路线图 / step4 调查报告 / s6m-a2a-optimization。
