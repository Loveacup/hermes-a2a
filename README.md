# 🤝 hermes-a2a · Hermes Agent-to-Agent Protocol

> 🇺🇸 **Real-time cross-profile communication for the Hermes multi-agent system — capability discovery, synchronous task delegation, SSE streaming, and structured multi-agent discussion.** Built on Google A2A Protocol (Apache 2.0).
>
> 🇨🇳 **为 Hermes 多 Profile 系统提供实时跨部门通信 — 能力自动发现、同步任务委派、SSE 流式响应、结构化多 Agent 讨论。** 基于 Google A2A 协议。

[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![A2A](https://img.shields.io/badge/A2A-v1.0-green)](https://github.com/a2aproject/A2A)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Profiles](https://img.shields.io/badge/profiles-16%2F16-brightgreen)]()
[![Status](https://img.shields.io/badge/status-production-brightgreen)]()

---

## 📖 Table of Contents

- [Why hermes-a2a?](#-why-hermes-a2a)
- [How It Compares](#-how-it-compares)
- [Architecture](#-architecture)
- [Monorepo Structure](#-monorepo-structure)
- [Quick Start](#-quick-start)
- [A2A Endpoints](#-a2a-endpoints)
- [Discussion Mode](#-discussion-mode)
- [Governance Role](#-governance-role)
- [Status & Roadmap](#-status--roadmap)
- [Docs](#-docs)
- [License](#-license)

---

## ✨ Why hermes-a2a?

| You need... | How hermes-a2a solves it |
|-------------|--------------------------|
| **Cross-profile communication** | 16 profiles connected via A2A — cap discovery, task delegation, SSE stream |
| **Production-grade reliability** | launchd KeepAlive with ~1s auto-recovery on crash |
| **Structured agent discussion** | Dual-mode: ROLEPLAY (bilateral debate) + SYNTHESIZE (comprehensive analysis) |
| **Clean separation of concerns** | `core/` is protocol-only (vendor anywhere); `s6m-config/` is governance-specific |
| **Auditable deployments** | Per-profile plists, port map, ADR methodology, full audit trail |
| **Zero-config doctor** | `hermes-a2a-doctor.sh` aggregates health across all profiles in one shot |

> **What makes this different:** Most A2A implementations are SDKs or single-agent gateways. hermes-a2a is a **governance-grade deployment** — 16 agents, synchronized launchd supervision, and a decomposition principle that lets any Hermes user adopt the protocol without importing the governance layer.

---

## 📊 How It Compares

| | hermes-a2a | [openclaw-a2a-gateway](https://github.com/win4r/openclaw-a2a-gateway) | [A2A-MCP-Server](https://github.com/GongRzhe/A2A-MCP-Server) | [a2a-inspector](https://github.com/a2aproject/a2a-inspector) |
|---|:---:|:---:|:---:|:---:|
| **A2A version** | v1.0 | v0.3.0 | v0.2.0 | v1.0 |
| **Scale** | 16 agents | 1 gateway | bridge | validation tools |
| **Governance tier** | ✅ launchd + port map + audits | ❌ | ❌ | ❌ |
| **Multi-agent discussion** | ✅ ROLEPLAY + SYNTHESIZE | ❌ | ❌ | ❌ |
| **Zero-config health** | ✅ doctor.sh | ❌ | ❌ | ❌ |
| **Platform** | Hermes plugin | OpenClaw plugin | MCP bridge | CLI tool |
| **Language** | Python | TypeScript | Python | TypeScript |
| **⭐ Stars** | — | 506 | 148 | 424 |

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Telegram 内阁群 (chat -5133970461)      │
│              default (小黄) ←→ regent (太子)               │
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

Two communication channels:
1. **API Server** (`:8642`/`:8643`) — Telegram messaging gateway (default + regent only)
2. **A2A Gateway** (per-profile port) — internal agent-to-agent protocol (all 16 profiles)

> **Decomposition principle:** `core/` is pure A2A — no profile names, ports, or governance — vendor it into any Hermes instance. `s6m-config/` contains the 三省六部 deployment layer (plists, port-map, ADRs, audits) — isolated so upstream protocol and downstream deployment evolve independently.

---

## 📁 Monorepo Structure

```
hermes-a2a/
├── core/                        ← 🔧 Protocol kernel (zero governance deps)
│   ├── plugin.py                → Hermes plugin entry (register)
│   ├── server.py                → A2A HTTP Server (health, agent-card, tasks, SSE)
│   ├── agent_card.py            → Auto-generated Agent Card
│   ├── task_handler.py          → Dual-mode: native /v1/runs or hermes chat -q
│   ├── discuss.py               → ROLEPLAY + SYNTHESIZE discussion engine
│   ├── plugin.yaml              → Plugin metadata
│   ├── requirements.txt         → pyyaml
│   ├── templates/a2a-launchd.plist → {{PROFILE}}/{{PORT}}/{{HERMES_HOME}} template
│   ├── scripts/
│   │   ├── hermes-a2a-doctor.sh → Aggregate health checks (reads port-map)
│   │   └── seed-a2a-symlinks.sh → Per-profile symlink seeder
│   └── README.md                → Kernel docs (for general Hermes users)
│
├── s6m-config/                  ← 🏯 三省六部 deployment config (governance-specific)
│   ├── plists/                  → 16 launchd plist files
│   ├── port-map.md              → Profile → port quick reference
│   ├── discuss-modes.yaml       → Discussion mode configuration
│   ├── docs/
│   │   ├── methodology.md       → ADR-001~004
│   │   ├── deployment-report.md
│   │   ├── architecture-comparison.md
│   │   ├── s6m-a2a-optimization.md
│   │   └── audits/              → Audit trail
│   └── README.md                → Deployment docs (for system operators)
│
├── README.md                    ← You're reading this
└── CLAUDE.md                    ← AI collaboration reference
```

---

## 🚀 Quick Start

### I just want to use A2A on my Hermes

```bash
# 1. Copy the core plugin
cp -r core/ ~/.hermes/plugins/hermes-a2a/

# 2. Generate a launchd plist from template and load it
#    (replace PROFILE and PORT with your values)
sed 's/{{PROFILE}}/my-profile/g; s/{{PORT}}/8900/g; s|{{HERMES_HOME}}|~/.hermes|g' \
  core/templates/a2a-launchd.plist > ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist

HOME=$HOME launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.my-profile.plist

# 3. Verify
curl http://127.0.0.1:8900/health
curl http://127.0.0.1:8900/a2a/.well-known/agent-card.json | jq
```

### I operate the 三省六部 deployment

Read [s6m-config/README.md](s6m-config/README.md) — full steps, port map, and failure recovery guide.

```bash
# Health check across all 16 profiles
bash core/scripts/hermes-a2a-doctor.sh

# Restart a single profile after core/ changes
HOME=$HOME launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
HOME=$HOME launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
curl http://127.0.0.1:8939/health
```

---

## 📡 A2A Endpoints

Every profile exposes:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/a2a/.well-known/agent-card.json` | Capability discovery (A2A 1.0 spec) |
| `POST` | `/a2a/tasks` | Submit task → returns `task_id` immediately |
| `GET` | `/a2a/tasks/{id}` | Poll status + artifact |
| `GET` | `/a2a/tasks/{id}/stream` | SSE event stream |

Port map: `s6m-config/port-map.md`

---

## 💬 Discussion Mode

hermes-a2a includes a structured multi-agent discussion engine (`core/discuss.py`):

| Mode | Behavior | Use case |
|------|----------|----------|
| **ROLEPLAY** | Two agents debate from assigned perspectives | Policy trade-offs, risk assessment |
| **SYNTHESIZE** | Multiple agents contribute → single comprehensive analysis | Strategic reviews, audit findings |

Used in the 内阁 Telegram group (chat `-5133970461`) for bilateral default↔regent deliberation.

---

## 🏯 Governance Role

hermes-a2a connects the following key roles in the 三省六部 governance system:

| Role | Profile | A2A Port | Identity |
|------|---------|:--------:|----------|
| **Crown Prince** | `regent` | 8939 | Central coordinator — receives directives, drafts orders, delegates, audits |
| **Xiao Huang** | `default` | 8945 | Alex's personal assistant — **independent** of 三省六部, equal peer to regent |
| 14 departments | — | see port-map | Operational departments (工部, 刑部, 户部, etc.) |

> **Xiao Huang's identity:** 小黄 is Alex's personal secretary, not part of any 三省六部 department. They collaborate with the Crown Prince as **equals**, not subordinates. In A2A discussions, 小黄 provides independent analysis, signs as `【小黄】`.

---

## 📊 Status & Roadmap

- [x] 16/16 A2A endpoints deployed + 2/2 API Servers healthy
- [x] launchd KeepAlive — crash recovery in ~1s
- [x] Dual-mode task execution (native `/v1/runs` + subprocess `hermes chat -q`)
- [x] A2A 1.0 spec compliance (id / name / description / examples / tags)
- [x] ROLEPLAY + SYNTHESIZE discussion modes
- [x] Monorepo split: `core/` (vendor-ready) vs `s6m-config/` (deployment-specific)
- [x] Doctor script: `hermes-a2a-doctor.sh` (aggregate health, supports `--port-map`)
- [ ] **Step 3:** EmpireThread event bridge (MEMORY_QUERY → Hindsight)

---

## 📚 Docs

| Document | For |
|----------|-----|
| [core/README.md](core/README.md) | Plugin developers, general Hermes users |
| [s6m-config/README.md](s6m-config/README.md) | System operators, deployment |
| [s6m-config/docs/methodology.md](s6m-config/docs/methodology.md) | Architecture decisions (ADR-001~004) |
| [s6m-config/docs/s6m-a2a-optimization.md](s6m-config/docs/s6m-a2a-optimization.md) | Optimization roadmap |
| [s6m-config/docs/audits/](s6m-config/docs/audits/) | Audit trail |
| [CLAUDE.md](CLAUDE.md) | AI agent collaboration guide |

---

## 📄 License

Apache-2.0 — see [LICENSE](LICENSE).
