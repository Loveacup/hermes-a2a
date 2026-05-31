---
project: hermes-a2a
status: v0.2.0 稳定 — 16/16 全绿，T2.5b Gateway 加固完成
created: 2026-05-27
modified: 2026-05-31
repo: https://github.com/finalhour/hermes-a2a
---

# hermes-a2a：三省六部 A2A 互通协议

## 概述

基于 Google A2A Protocol (agent-to-agent, 23.4K⭐) 的 Hermes 全局插件。为三省六部 16 个 profile 提供标准化的跨部门通信能力——能力发现、任务委派、进度流式传输。

## 动机

当前三省六部 profile 间通信仅靠 Kanban 卡（异步投递 + 60s 轮询），无法做同步 on-demand 查询。内阁 API 桥接（default↔regent 的 localhost HTTP）已验证同步通信模式的可行性。hermes-a2a 将此模式标准化、规模化。

## 当前状态：v0.2.0 生产稳定

- **A2A 端口**：16/16 全部返回 200，每 profile 13 skills 可用
- **Gateway**：regent(8417) + default(8460) + cron-worker(8461) 全部监管运行
- **执行模式**：全 16 profile 走 api_server 模式（task_handler 已去白名单）
- **进程管理**：A2A server 在 gateway 内嵌运行（非独立 launchd），端口冲突自动 skip
- **Kanban**：0 active（全清，integrity=ok）
- **E2E**：6/6 文件全部通过（含 DCI pipeline、辩论、16-profile 矩阵）
- **金字塔**：L3 44/44 + L4 5/5 = 49 项全绿

### 16 Profile 部署清单

端口公式 A2A：`8650 + sha256(profile) % 300`（零碰撞）
端口公式 API：`8400 + sha256("api:"+profile) % 100`（salted，零碰撞）

| Profile | 角色 | A2A | API | Skills |
|---------|------|:---:|:---:|:------:|
| jiangzuojian | 将作监/校验 | 8654 | 8425 | 5 |
| auditor | 御史台/复审 | 8698 | 8468 | 3 |
| hanlinyuan | 翰林院/知识 | 8702 | 8466 | 6 |
| dispatcher | 派工调度 | 8707 | 8465 | 4 |
| engineer | 兵部/工程 | 8718 | 8482 | 5 |
| planner | 策划 | 8728 | 8474 | 2 |
| tester | 测试 | 8755 | 8480 | 4 |
| reviewer | 御史 | 8761 | 8493 | 2 |
| archivist | 史馆/归档 | 8804 | 8431 | 2 |
| shangshu | 尚书/协调 | 8826 | 8492 | 4 |
| protocol | 礼部/协议 | 8833 | 8443 | 3 |
| gongbu | 工部/基建 | 8898 | 8458 | 4 |
| registry | 吏部/注册 | 8928 | 8438 | 3 |
| budget | 户部/成本 | 8936 | 8445 | 3 |
| regent | 太子/监国 | 8939 | 8417 | 13 |
| default | 小黄/秘书 | 8945 | 8460 | 13 |

> **注**：API Server 端口仅 regent(8417) + default(8460) 实际启用；其余 profile 无 API Server 需求（只做 A2A/Kanban worker），端口已分配待按需启用。

## 架构

```
┌──────────────────────────────────────────────────┐
│                  hermes-a2a v0.2.0                 │
│                                                    │
│  Agent Card ──→ 能力清单（自动从 config 生成）      │
│  Task       ──→ 工作单元（id + status + artifact）  │
│  Stream     ──→ SSE 流式进度                        │
│  Discuss    ──→ ROLEPLAY + SYNTHESIZE 讨论编排     │
│  EmpireThread ──→ pre_tool_call → Obsidian + Supermemory │
│                                                    │
│  Transport: HTTP/JSON (future: JSON-RPC 2.0)       │
└──────────────────────────────────────────────────┘
```

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
├── core/                        # 通用 A2A 内核（不绑三省六部）
│   ├── plugin.py                # Hermes 插件入口 (register)
│   ├── server.py                # A2A HTTP Server
│   ├── agent_card.py            # Agent Card 自动生成
│   ├── task_handler.py          # 双模执行（API Server / subprocess）
│   ├── auth.py                  # Bearer token 认证
│   ├── registry.py              # 端口注册表 + CLI
│   ├── paths.py                 # HOME 劫持穿透
│   ├── identity.py              # Profile 身份注入
│   ├── port_resolver.py         # 端口公式计算
│   ├── rate_limiter.py          # 速率限制
│   ├── storage.py               # 任务存储
│   ├── discuss.py               # 讨论编排引擎
│   ├── auto_discuss.py          # 自动讨论触发
│   ├── a2a_dispatch.py          # Profile 推荐打分
│   ├── swarm_wrapper.py         # Kanban swarm 封装
│   ├── skill_resolver.py        # M2CL 4 层 skill 加载
│   ├── skill_sanitizer.py       # Unknown skill 拦截
│   ├── comment_kind.py          # DCI 14 kind 枚举
│   ├── comment_kind_backfill.py # 历史评论分类回填
│   ├── comment_kind_classifier.py # LLM 分类器
│   ├── orchestrator_router.py   # ROUTE_BY_KIND + VoteTally + deadlock
│   ├── audit_hook.py            # 外科切除后保留 score+alert
│   ├── empire_emit.py           # EmpireThread emit 入口
│   ├── gateway.py               # Gateway 进程管理
│   ├── plugin.yaml              # 插件元数据
│   ├── requirements.txt         # pyyaml
│   ├── event_bridge/            # 事件桥子系统
│   │   ├── core.py / daemon.py / dlq.py / cursor.py
│   │   └── sinks/{obsidian,supermemory}.py
│   ├── scripts/
│   │   ├── hermes-a2a-doctor.sh
│   │   └── seed-a2a-symlinks.sh
│   └── templates/
│       ├── a2a-launchd.plist
│       ├── api-server-launchd.plist
│       └── event-bridge-launchd.plist
│
├── s6m-config/                  # 三省六部专属配置
│   ├── port-map.md              # 16 profile 端口快查
│   ├── discuss-modes.yaml       # 讨论模式配置
│   └── docs/
│       ├── tracking.md          # 本文件
│       ├── methodology.md       # ADR-001~006
│       ├── tdd-test-plan.md     # TDD 测试计划
│       ├── tdd-plan-review.md   # TDD 审查报告
│       └── design/              # 设计方案
├── scripts/
│   └── gateway-wrapper.sh       # preflight + killpg 拦截
│ ├── tests/                       # E2E + 单元测试
│ │   ├── e2e/
│ │   │   ├── test_l3_s2_code_review.py       # S2 代码审查 10/10
│ │   │   ├── test_l3_s3_morning_news.py      # S3 早新闻 7/7
│ │   │   ├── test_l3_s4_governance_change.py # S4 制度修改 8/8
│ │   │   ├── test_l3_s5_double_rebuke.py     # S5 双次封驳 9/9
│ │   │   └── test_l4_nonfunctional.py        # L4 非功能 5/5
│ │   └── unit/
├── CLAUDE.md
└── README.md
```

## 关联

- A2A Protocol: https://github.com/a2aproject/A2A
- 三省六部宪章: `three-provinces-constitution` skill
- EmpireThread: Obsidian + Supermemory 双 Sink
- Kanban: Hermes 原生 kanban_db，dispatcher 内嵌 gateway
- 3S6M Plugin: 设计文档已就绪（Ob §12），待构建（2026-05-30 前置调研完成）

## 日志

- 2026-05-27: 项目立项。创建 GitHub 仓库 + Obsidian 文档。
- 2026-05-27: Step 1 核心 4 profile 部署 + regent/default API Server 接入。
- 2026-05-27: 首轮 CC agent team 审计 — 发现 P0×3（端口碰撞/task_handler 未接通/无看门狗）。
- 2026-05-27: P0/P1/P2 修复（PORT_RANGE 50→200, threading 接通, launchd 全量监管, SKILL_MAP A2A 1.0, 双模执行, seed symlink 脚本）。
- 2026-05-27: 二轮 CC 3-agent team 审计 — 确认 P0-2/P0-3 已修, 发现 NEW-P0-A（源码未同步）+ NEW-P0-B（PORT_RANGE=200 仍有碰撞）。
- 2026-05-27: NEW-P0-A/B 修复 — PORT_RANGE 200→300（零碰撞）, 源码 ↔ 部署 ↔ GitHub 三同步, commit `d4b73a4`。v0.1.1 生产就绪。
- 2026-05-28: Monorepo 拆分 — core/（通用内核）+ s6m-config/（三省六部配置）, commit `6a4ce85`。
- 2026-05-28: Obsidian 全量同步 — 11 份文档入 vault。
- 2026-05-28: 结果语义升级 — task_handler 增加语义状态 + 回退机制。
- 2026-05-28: 讨论模式 — core/discuss.py（ROLEPLAY + SYNTHESIZE）, commit `dabddec`。
- 2026-05-30: EmpireThread + API Server 季度收官 — EventBridge Supermemory 替换 Hindsight（ADR-005, 52 测试全绿），API Server 16-profile 公式化（commit `9c33e55`, 146/146 回归）。详见 [[EmpireThread_v2_API_Server_实施总结_20260530]]。
- 2026-05-30: P0 三项 TDD 全绿 — P0-1 Kanban init（12 tests）、P0-2 M2CL skill resolver（14 tests）、P0-3 DCI 旁路表（13 tests），commit `9b59c4f`。详见 [[三省六部A2A_TDD实施总结_20260529]]。
- 2026-05-30: E2E 全绿收尾 — 6/6 E2E 文件全部通过（A3 skill_sanitizer + B DCI 闭合 + C 真 kanban 验证），commit `9b59c4f`。
- 2026-05-30: P0 E2E 真 LLM 矩阵 + 辩论 — 16/16 api_server 模式全绿，辩论分类器+路由 6/6 全绿，commit `afa7368` + `a9957cf`。
- 2026-05-30: T2.5b Gateway 稳定性加固 — gateway-wrapper.sh（preflight + killpg），3 core gateway launchd 监管，cron-worker 端口分离，commit `bf06a35`。
- 2026-05-30: EmpireThread 事件桥实施 — P0-1 emit hook 全链路闭环（pre_tool_call → JSONL → daemon → Obsidian + Supermemory），commit `13e7d2c`。
- 2026-05-30: Step 2 部署完成 — 16/16 v0.2.0 全部运行, P0 registry plist 修复, MCP 瘦身, commit `d075d4a`。
- 2026-05-30: 环境清理 — 部署同步（rsync 31 文件 core/ → 部署），Kanban 残留任务 blocked，跟踪文档全量更新。
- 2026-05-30: 3S6M 插件前置调研 — Hermes 插件系统能力验明：ctx.register_skill() 支持 plugin: 命名空间（优于 §12 设计），A2A 进程管理仍需 launchd。设计搁置，待父皇召唤。
- 2026-05-31: **L3 E2E 全场景完成** — S2(代码审查 10/10 998s)、S3(早新闻 7/7 396s)、S4(制度修改 8/8 632s)、S5(双次封驳 9/9 903s)，TDD 全链验证。7 个 commit，5 个 E2E 测试文件。详见 [[三省六部全面测试方案_20260530]]。
- 2026-05-31: **L4 非功能测试完成** — Auth 强制(2/2)、跨 profile 隔离(1/1)、宕机恢复(2/2)，5/5 <1min，commit `cd6362c`。L0-L4 金字塔全面建成。
- 2026-05-31: **L3-S1 健康扫描 E2E 完成** — planner→reviewer→shangshu→budget∥gongbu→protocol→tester→reviewer→archivist 全 9 步链，10/10 ALL GREEN。修复：gongbu 超时 480→900s、DeepSeek credits 耗尽→切 provider、gongbu 任务精简。commit `eb18a9c`。
- 2026-05-31: **SWAP 看门狗确认** — SWAP 55% 正常，sysctl 在 cron sandbox 可用（旧误报已消解），无需修复。
- 2026-05-31: **Supermemory re-tag 完成** — 5 篇旧文档标签 `hermes_cabinet`→`hermes-cabinet` 修复（Dashboard 手动），`_sanitize_tag` 已修复新文档不再受影响。
- 2026-05-31: **L3 E2E 金字塔全绿** — S1(10/10) + S2(10/10) + S3(7/7) + S4(8/8) + S5(9/9) = 44/44 全绿，L4 5/5，合计 49 项全通。[[E2E测试结果汇总_20260531]]
