# 🏯 hermes-s6m-a2a · 三省六部 × A2A

> **Hermes 多 Agent 治理体系——16 个 Profile 分权协作，三省六部流程编排，Kanban 任务调度，EmpireThread 事件溯源。基于 Google A2A Protocol（Apache 2.0）。**
>
> **16 agents, real-time capability discovery, synchronous task delegation, structured debate, full audit trail. Built for production-grade multi-agent governance.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![A2A](https://img.shields.io/badge/A2A-v1.0-green)](https://github.com/a2aproject/A2A)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Profiles](https://img.shields.io/badge/profiles-16%2F16-brightgreen)]()
[![Tests](https://img.shields.io/badge/tests-49%2F49-brightgreen)]()

---

## 这是什么？

**三省六部（3S6M）不是"挂 16 个 Agent 同时回答"——它是一套分权治理制度。** 核心思想来自唐代三省六部制：中书拟制、门下封驳、尚书派工、六部执行、御史监察、史馆归档。

`hermes-s6m-a2a` 是这套制度的**通信主干**：16 个 Hermes Profile 通过 A2A 协议实时互通，Kanban 负责任务审计链，EmpireThread 记录全链路事件。

```
                     用户（父皇）
                         │
                   监国太子 (regent)
                         │
       ┌─────────────────┼─────────────────┐
       │ 中书省 planner   门下省 reviewer   尚书省 shangshu
       │ 拟制              封驳              派工调度
       └─────────────────┴─────────────────┘
                         │
       ┌───┬───┬───┬───┬───┴───┬───────┬───────┐
       吏   户   礼   兵   刑    工     御史   史馆
```

**关键原则**：分权而非堆 Agent · 结构化任务书取代共享上下文 · 先星型后有限互通 · 显式 allowlist 非 blocklist。

完整方法论见 [`methodology.md`](s6m-config/docs/methodology.md)（原三省六部×A2A 方法论文档 v2.0）。

---

## 为什么不是普通 A2A 实现？

| 普通 A2A 实现 | hermes-s6m-a2a |
|-------------|----------------|
| SDK / 单 Agent 网关 | **16 Agent 治理级部署** |
| 无流程编排 | **三省六部全链路（拟→审→派→办→查→归）** |
| 无测试体系 | **L0-L4 五层金字塔，49 项全绿** |
| 无审计 | **Kanban 任务链 + EmpireThread 事件溯源** |
| 单语言/单框架 | **`core/` 纯协议（可独立复用）+ `s6m-config/` 治理配置** |

---

## 核心数字

| 指标 | 数值 |
|------|:----|
| Profile 总数 | 16（三省 + 六部 + 御史台 + 史馆 + 翰林院 + 将作监 + 司验院） |
| A2A 端口 | 16/16 全在线，hash 分配零碰撞 |
| 测试金字塔 | L0-L4 五层，49/49 全绿 |
| E2E 场景 | 健康扫描 · 代码审查 · 早新闻 · 制度修改 · 双次封驳 |
| 治理流程 | 监国承旨 → 中书拟制 → 门下封驳 → 尚书派工 → 六部施行 → 御史监察 → 史馆归档 |

---

## 仓库结构

```
hermes-s6m-a2a/
├── core/                     ← 🔧 纯净 A2A 协议内核（无治理依赖，任何 Hermes 用户可用）
│   ├── server.py             → A2A HTTP Server
│   ├── agent_card.py         → 能力自动发现
│   ├── task_handler.py       → 双模任务执行
│   ├── discuss.py            → ROLEPLAY + SYNTHESIZE 讨论引擎
│   ├── scripts/
│   │   ├── hermes-a2a-doctor.sh  → 一键健康聚合
│   │   └── seed-a2a-symlinks.sh  → Per-profile symlink 种子
│   └── README.md
│
├── s6m-config/               ← 🏯 三省六部专属配置
│   ├── plists/               → 16 个 launchd plist
│   ├── port-map.md           → 端口快查表
│   ├── docs/
│   │   ├── methodology.md    → 方法论文档 v2.0（架构 · 通信 · 治理 · 测试 · 运维 · ADR）
│   │   ├── tracking.md       → 全量实施日志
│   │   ├── audits/           → 审计历史
│   │   └── design/           → EmpireThread · 路线图 · 架构方案
│   └── README.md
│
├── scripts/
│   └── gateway-wrapper.sh    → Gateway preflight + killpg 监管
│
├── tests/
│   └── end_to_end/           → L3 S1-S5 + L4 E2E 测试（49/49）
│
└── README.md                 ← 本文件
```

---

## 快速上手

### 给 Hermes 用户（只想要 A2A，不需要治理层）

```bash
# 拷贝协议内核
cp -r core/ ~/.hermes/plugins/hermes-a2a/

# 生成启动配置
sed 's/{{PROFILE}}/my-profile/g; s/{{PORT}}/8900/g' \
  core/templates/a2a-launchd.plist > ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist

# 启动并验证
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist
curl http://127.0.0.1:8900/health
```

### 给三省六部运维者

```bash
# 全量健康检查
bash core/scripts/hermes-a2a-doctor.sh

# 重启单个 Profile（改 core 后）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
```

端口快查：`s6m-config/port-map.md`

---

## A2A 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 存活检测 |
| `GET` | `/a2a/.well-known/agent-card.json` | 能力发现 |
| `POST` | `/a2a/tasks` | 提交任务 |
| `GET` | `/a2a/tasks/{id}` | 查询状态 |
| `GET` | `/a2a/tasks/{id}/stream` | SSE 流式进度 |

---

## 治理流程（三省六部全链路）

```
监国接旨 → planner 拟制方案 → reviewer 封驳审查
    ↓ 封驳通过
shangshu 拆解派工 → 六部并行执行 → auditor 稽核
    ↓
archivist 归档 → Obsidian + Supermemory
```

**封驳与返修**：门下省可驳回不合规方案（≤2 轮返修），双次封驳韧性已验证（S5 E2E 9/9）。

---

## 路线图

- [x] 16 Profile A2A 全量部署（v0.2.0）
- [x] ROLEPLAY + SYNTHESIZE 讨论模式
- [x] EmpireThread 事件桥（pre_tool_call → Obsidian + Supermemory）
- [x] L0-L4 五层测试金字塔（49/49 全绿）
- [x] Gateway 监管（launchd + preflight + killpg）
- [ ] L4 性能压测（16 Profile 并发 + Kanban 100 卡）
- [ ] 早新闻 Cron 化（每日自动生成投递）
- [ ] 3S6M 插件构建

---

## 许可

Apache-2.0 — [LICENSE](LICENSE)
