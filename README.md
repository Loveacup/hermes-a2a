# 🤝 hermes-a2a · Hermes Agent-to-Agent Protocol

> 🇺🇸 **Real-time cross-profile communication for the Hermes multi-agent system — capability discovery, synchronous task delegation, SSE streaming, and structured multi-agent discussion.** Built on Google A2A Protocol (Apache 2.0).
>
> 🇨🇳 **为 Hermes 多 Profile 系统提供实时跨部门通信：能力自动发现、同步任务委派、SSE 流式响应、结构化多 Agent 讨论。** 基于 Google A2A 协议。

[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![A2A](https://img.shields.io/badge/A2A-v1.0-green)](https://github.com/a2aproject/A2A)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Profiles](https://img.shields.io/badge/profiles-16%2F16-brightgreen)]()
[![Status](https://img.shields.io/badge/status-production-brightgreen)]()

---

## 📖 Table of Contents · 目录

- [Why hermes-a2a? · 为什么需要它？](#-why-hermes-a2a--为什么需要它)
- [How It Compares · 同类对比](#-how-it-compares--同类对比)
- [Architecture · 架构](#-architecture--架构)
- [Monorepo Structure · 仓库结构](#-monorepo-structure--仓库结构)
- [Quick Start · 快速上手](#-quick-start--快速上手)
- [A2A Endpoints · 协议端点](#-a2a-endpoints--协议端点)
- [Discussion Mode · 讨论模式](#-discussion-mode--讨论模式)
- [Governance Role · 治理角色](#-governance-role--治理角色)
- [Status & Roadmap · 状态与路线图](#-status--roadmap--状态与路线图)
- [Docs · 文档](#-docs--文档)
- [License · 许可](#-license--许可)

---

## ✨ Why hermes-a2a? · 为什么需要它？

> Before hermes-a2a, Hermes profiles relied on Kanban for async communication. A2A adds **real-time synchronous capability** — agents can discover each other, delegate tasks, and hold structured discussions.

> 在 hermes-a2a 之前，Hermes 各 profile 仅靠 Kanban 异步通信。A2A 加入后实现了**实时同步能力**——Agent 间可互相发现、委派任务、进行结构化讨论。

| You need... · 你需要... | How hermes-a2a solves it · hermes-a2a 如何解决 |
|--------------------------|---------------------------------------------|
| **Cross-profile communication** · 跨 profile 通信 | 16 profiles connected via A2A — cap discovery, task delegation, SSE stream · 16 个 profile 通过 A2A 互联 |
| **Production-grade reliability** · 生产级可靠性 | launchd KeepAlive with ~1s auto-recovery on crash · 崩溃 ~1 秒自动复活 |
| **Structured agent discussion** · 结构化讨论 | Dual-mode: ROLEPLAY (bilateral debate) + SYNTHESIZE (comprehensive) · 双模：ROLEPLAY + SYNTHESIZE |
| **Clean separation of concerns** · 关注点分离 | `core/` is protocol-only (vendor anywhere); `s6m-config/` is governance-specific · core/ 纯协议（可独立复用）、s6m-config/ 专属配置 |
| **Auditable deployments** · 可审计部署 | Per-profile plists, port map, ADR methodology, full audit trail · 每 profile plist + 端口表 + ADR + 审计历史 |
| **Zero-config health** · 零配置健康检查 | `hermes-a2a-doctor.sh` aggregates health across all profiles · 一键聚合 16 profile 健康状态 |

> **What makes this different · 差异化：** Most A2A implementations are SDKs or single-agent gateways. hermes-a2a is a **governance-grade deployment** — 16 agents, synchronized launchd supervision, and a decomposition principle that lets any Hermes user adopt the protocol without importing the governance layer.

> **大多数 A2A 实现只是 SDK 或单 Agent 网关。** hermes-a2a 是**治理级部署**——16 个 Agent、统一 launchd 监管、core/s6m-config 拆分机制让任何 Hermes 用户都能直接使用协议内核，无需引入治理层。

---

## 📊 How It Compares · 同类对比

| | hermes-a2a | [openclaw-a2a-gateway](https://github.com/win4r/openclaw-a2a-gateway) | [A2A-MCP-Server](https://github.com/GongRzhe/A2A-MCP-Server) | [a2a-inspector](https://github.com/a2aproject/a2a-inspector) |
|---|:---:|:---:|:---:|:---:|
| **A2A version** · 协议版本 | v1.0 | v0.3.0 | v0.2.0 | v1.0 |
| **Scale** · 规模 | 16 agents | 1 gateway | bridge | validation tools |
| **Governance tier** · 治理层 | ✅ launchd + port map + audits | ❌ | ❌ | ❌ |
| **Multi-agent discussion** · 多 Agent 讨论 | ✅ ROLEPLAY + SYNTHESIZE | ❌ | ❌ | ❌ |
| **Zero-config health** · 零配置健康 | ✅ doctor.sh | ❌ | ❌ | ❌ |
| **Platform** · 平台 | Hermes plugin | OpenClaw plugin | MCP bridge | CLI tool |
| **Language** · 语言 | Python | TypeScript | Python | TypeScript |
| **Stars** · 星标 | — | 506 | 148 | 424 |

---

## 🏗 Architecture · 架构

```
┌──────────────────────────────────────────────────────────┐
│              Telegram 内阁群 (chat -5133970461)            │
│          default (小黄) ←→ regent (太子)                   │
└──────────────┬─────────────────────┬────────────────────┘
               │                     │
     ┌─────────▼─────────┐  ┌───────▼──────────┐
     │  Hermes API Server │  │  Hermes A2A GW   │
     │  :8642 (default)   │  │  :8945 (default) │
     │  :8643 (regent)    │  │  :8939 (regent)  │
     └───────────────────┘  └──────┬────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼───────┐  ┌────────▼────────┐
     │ Hermes A2A GW   │  │ Hermes A2A GW │  │  ... 13 more    │
     │ :8933 (gongbu)  │  │ :8934 (tester)│  │  profiles        │
     └─────────────────┘  └───────────────┘  └─────────────────┘
```

Two communication channels · 两条通信通道：

1. **API Server** (`:8642`/`:8643`) — Telegram messaging gateway (default + regent only) · Telegram 消息网关（仅 default + regent）
2. **A2A Gateway** (per-profile port) — internal agent-to-agent protocol (all 16 profiles) · 内部 Agent 间协议（全 16 profile）

> **Decomposition principle · 拆分原则：** `core/` is pure A2A — no profile names, ports, or governance — vendor it into any Hermes instance. `s6m-config/` contains the 三省六部 deployment layer (plists, port-map, ADRs, audits) — isolated so upstream protocol and downstream deployment evolve independently.

> **`core/` 是纯净的 A2A 协议代码**——不含任何 profile 名、端口号、部门名称，任何 Hermes 用户都能独立使用。**`s6m-config/` 包含三省六部专属部署配置**——plist、端口表、ADR、审计——与核心协议分离，上下游可以独立演进。

---

## 📁 Monorepo Structure · 仓库结构

```
hermes-a2a/
├── core/                        ← 🔧 Protocol kernel (zero governance deps)
│   │                               · 协议内核（无治理依赖）
│   ├── plugin.py                → Hermes plugin entry (register) · 插件入口
│   ├── server.py                → A2A HTTP Server · HTTP 服务器
│   ├── agent_card.py            → Auto-generated Agent Card · 自动生成能力卡
│   ├── task_handler.py          → Dual-mode task execution · 双模任务执行
│   ├── discuss.py               → ROLEPLAY + SYNTHESIZE engine · 讨论引擎
│   ├── plugin.yaml              → Plugin metadata · 插件元数据
│   ├── requirements.txt         → pyyaml
│   ├── templates/a2a-launchd.plist → {{PROFILE}}/{{PORT}}/{{HERMES_HOME}} template
│   ├── scripts/
│   │   ├── hermes-a2a-doctor.sh → Aggregate health checks · 健康聚合
│   │   └── seed-a2a-symlinks.sh → Per-profile symlink seeder · Symlink 种子
│   └── README.md                → Kernel docs · 内核文档
│
├── s6m-config/                  ← 🏯 Deployment config (governance-specific)
│   │                               · 三省六部部署配置
│   ├── plists/                  → 16 launchd plist files · 16 个 plist
│   ├── port-map.md              → Profile → port quick reference · 端口快查
│   ├── discuss-modes.yaml       → Discussion mode config · 讨论模式配置
│   ├── docs/
│   │   ├── methodology.md       → ADR-001~004
│   │   ├── deployment-report.md → 部署报告
│   │   ├── architecture-comparison.md
│   │   ├── s6m-a2a-optimization.md → 优化方案
│   │   └── audits/              → Audit trail · 审计历史
│   └── README.md                → Deployment docs · 部署文档
│
├── README.md                    ← You're reading this · 你在读这个
└── CLAUDE.md                    ← AI collaboration reference · AI 协作文档
```

---

## 🚀 Quick Start · 快速上手

### General Hermes user · 一般 Hermes 用户

Want to add A2A to your own Hermes instance? · 想给自己的 Hermes 加 A2A？

```bash
# 1. Copy the core plugin · 拷贝核心插件
cp -r core/ ~/.hermes/plugins/hermes-a2a/

# 2. Generate a launchd plist from template · 从模板生成 plist
#    (replace PROFILE and PORT · 替换 PROFILE 和 PORT)
sed 's/{{PROFILE}}/my-profile/g; s/{{PORT}}/8900/g; s|{{HERMES_HOME}}|~/.hermes|g' \
  core/templates/a2a-launchd.plist > ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist

# 3. Load and verify · 加载并验证
HOME=$HOME launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist

curl http://127.0.0.1:8900/health
curl http://127.0.0.1:8900/a2a/.well-known/agent-card.json | jq
```

### 三省六部 operator · 体系运维者

Read [s6m-config/README.md](s6m-config/README.md) for full steps, port map, and failure recovery. · 完整步骤、端口表和故障恢复见 [s6m-config/README.md](s6m-config/README.md)。

```bash
# Health check across all 16 profiles · 全 16 profile 健康检查
bash core/scripts/hermes-a2a-doctor.sh

# Restart a single profile after core changes · 改 core 后重启单个 profile
HOME=$HOME launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
HOME=$HOME launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
curl http://127.0.0.1:8939/health
```

---

## 📡 A2A Endpoints · 协议端点

Every profile exposes · 每个 profile 暴露：

| Method | Path | Description · 说明 |
|--------|------|-------------------|
| `GET` | `/health` | Liveness probe · 存活检测 |
| `GET` | `/a2a/.well-known/agent-card.json` | Capability discovery (A2A 1.0 spec) · 能力发现 |
| `POST` | `/a2a/tasks` | Submit task → returns `task_id` immediately · 提交任务，立即返回 task_id |
| `GET` | `/a2a/tasks/{id}` | Poll status + artifact · 查询状态和产物 |
| `GET` | `/a2a/tasks/{id}/stream` | SSE event stream · SSE 事件流 |

Port map · 端口表: `s6m-config/port-map.md`

### New in v0.2.0 — Gateway + Observability

The **Registry Gateway** (`:8928`) acts as a unified reverse proxy for all 16 profile A2A endpoints:
- `GET /a2a/{profile}/.well-known/agent-card.json` → forwarded to profile
- `POST /a2a/{profile}/tasks` / `/tasks/send` → forwarded to profile
- `GET /health` → gateway liveness
- `GET /gateway/metrics` → proxied requests, backend errors, uptime

Per-profile metrics available at `GET /a2a/metrics` on every profile A2A server, with structured `key=value` logging for downstream aggregation.

---

## 💬 Discussion Mode · 讨论模式

hermes-a2a includes a structured multi-agent discussion engine · hermes-a2a 内置结构化多 Agent 讨论引擎 (`core/discuss.py`)：

| Mode · 模式 | Behavior · 行为 | Use case · 使用场景 |
|-------------|-----------------|-------------------|
| **ROLEPLAY** · 双边辩论 | Two agents debate from assigned perspectives · 两 Agent 从指定立场辩论 | Policy trade-offs, risk assessment · 政策权衡、风险评估 |
| **SYNTHESIZE** · 综合研判 | Multiple agents contribute → single comprehensive analysis · 多 Agent 贡献 → 综合研判 | Strategic reviews, audit findings · 战略审查、审计发现 |

Used in the 内阁 Telegram group (chat `-5133970461`) for bilateral default↔regent deliberation. · 在内阁 Telegram 群中用于 default↔regent 双边讨论。

---

## 🏯 Governance Role · 治理角色

hermes-a2a connects the following key roles in the 三省六部 governance system · hermes-a2a 在三省六部治理体系中连接以下关键角色：

| Role · 角色 | Profile | A2A Port | Identity · 身份 |
|-------------|---------|:--------:|-----------------|
| **Crown Prince** · 监国太子 | `regent` | 8939 | Central coordinator · 三省六部总枢 |
| **Xiao Huang** · 小黄 | `default` | 8945 | Alex's personal assistant — **independent** · 独立于三省六部 |
| 14 departments · 14 个部门 | — | see port-map | Operational departments · 各职能部门 |

> **小黄的身份：** 小黄是 Alex 的贴身秘书，**不属于三省六部任何部门**。他与太子是**平等协作**关系，非上下级。在 A2A 讨论中，小黄以独立视角提供分析，落款 `【小黄】`。

> **Xiao Huang's identity:** Xiao Huang is Alex's personal secretary, **not part of any 三省六部 department**. They collaborate with the Crown Prince as **equals**, not subordinates. In A2A discussions, Xiao Huang provides independent analysis and signs as `【小黄】`.

---

## 📊 Status & Roadmap · 状态与路线图

- [x] 16/16 A2A endpoints deployed + 2/2 API Servers healthy · 16/16 A2A + 2/2 API 已部署
- [x] launchd KeepAlive — crash recovery in ~1s · 崩溃 ~1s 复活
- [x] Dual-mode task execution (native `/v1/runs` + subprocess `hermes chat -q`) · 双模任务执行
- [x] A2A 1.0 spec compliance · A2A 1.0 合规
- [x] ROLEPLAY + SYNTHESIZE discussion modes · 讨论模式
- [x] Monorepo split: `core/` (vendor-ready) vs `s6m-config/` (deployment-specific) · Monorepo 拆分
- [x] Doctor script: `hermes-a2a-doctor.sh` · 一键健康检查
- [x] **v0.2.0:** Gateway reverse proxy (registry:8928) · 反向代理网关
- [x] **v0.2.0:** Rate limiter (per-profile leaky bucket) · 速率限制
- [x] **v0.2.0:** Capability-based dispatcher (CN/EN keyword scoring) · 能力分发
- [x] **v0.2.0:** Observability — `/a2a/metrics`, `/gateway/metrics`, structured logging · 可观测性
- [x] **v0.2.0:** Audit score hook — completion, speed, semantic, consistency scoring · 审计评分
- [x] **Step 3:** EmpireThread event bridge — design complete (CC 3-Agent evaluation + 5 hardening items) · 事件桥设计完成
- [ ] **Step 4:** EmpireThread event bridge — implementation (3 weeks, ~550 lines) · 事件桥实施
- [x] **v0.2.5:** All 16 profiles online (launchd plists, Python 3.9→3.11) · 全 profile 上线
- [x] **v0.2.6:** Audit closed-loop — low-score → Telegram alert + Kanban review card · 审计闭环

---

## 📚 Docs · 文档

| Document · 文档 | For · 适用 |
|----------------|-----------|
| [core/README.md](core/README.md) | Plugin developers, general Hermes users · 插件开发者 |
| [s6m-config/README.md](s6m-config/README.md) | System operators, deployment · 运维部署 |
| [s6m-config/docs/methodology.md](s6m-config/docs/methodology.md) | Architecture decisions (ADR-001~004) · 架构决策 |
| [s6m-config/docs/s6m-a2a-optimization.md](s6m-config/docs/s6m-a2a-optimization.md) | Optimization roadmap · 优化方案 |
| [s6m-config/docs/Hermes_路线图_v1.0.md](s6m-config/docs/Hermes_路线图_v1.0.md) | 🆕 Overall roadmap (5 directions + priorities) · 总体路线图 |
| [s6m-config/docs/EmpireThread_事件桥_综合设计文档_v1.0.md](s6m-config/docs/EmpireThread_事件桥_综合设计文档_v1.0.md) | 🆕 EmpireThread event bridge full design · 事件桥综合设计 |
| [s6m-config/docs/audits/](s6m-config/docs/audits/) | Audit trail · 审计历史 |
| [CLAUDE.md](CLAUDE.md) | AI agent collaboration guide · AI 协作指南 |

---

## 📄 License · 许可

Apache-2.0 — see [LICENSE](LICENSE). · 详见 [LICENSE](LICENSE)。
