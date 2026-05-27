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

### Step 1: 核心 4 profile（最小闭环）

| Profile | 端口 | 角色 |
|---------|------|------|
| shangshu | 8650 | 尚书省 API Hub |
| engineer | 8651 | 兵部 / 代码实现 |
| gongbu | 8652 | 工部 / 基础设施 |
| budget | 8653 | 户部 / 数据与成本 |

### Step 2: 全量部署（15 profile + A2A Agent Card）

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
~/.hermes/plugins/hermes-a2a/
├── __init__.py          # 模块入口
├── plugin.py            # Hermes 插件加载器
├── server.py            # A2A HTTP Server
├── agent_card.py        # Agent Card 自动生成
├── task_handler.py      # Task → Hermes agent 转发
├── requirements.txt     # pyyaml
└── README.md            # 本文件
```

## 关联

- A2A Protocol: https://github.com/a2aproject/A2A
- 三省六部宪章: `three-provinces-constitution` skill
- EmpireThread P4: capability_snapshot + heartbeat + MEMORY_QUERY
- 内阁 API 桥接: default↔regent localhost HTTP

## 日志

- 2026-05-27: 项目立项。创建 GitHub 仓库 + Obsidian 文档。Step 1 启动。
