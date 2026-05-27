# 🔗 hermes-a2a · Agent-to-Agent Protocol for 三省六部

> 🇺🇸 **Real-time cross-profile communication for the Hermes Agent governance system — capability discovery, synchronous task delegation, and streaming progress, all backed by launchd supervision and 16-profile zero-collision port allocation.**
>
> 🇨🇳 **为 Hermes Agent 三省六部治理体系打造的实时跨部门通信协议 — 能力自动发现、任务同步委派、进度流式传输，launchd 全量监管，16 profile 零碰撞端口分配。**

[![A2A Protocol](https://img.shields.io/badge/A2A-1.0-blue)](https://github.com/a2aproject/A2A)
[![Version](https://img.shields.io/badge/version-0.1.1-green)]()
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Profiles](https://img.shields.io/badge/profiles-6%2F15-orange)]()

---

## 📖 目录 · Table of Contents

- [Why · 为什么](#-why--为什么)
- [Live Status · 运行状态](#-live-status--运行状态)
- [Quick Start · 快速开始](#-quick-start--快速开始)
- [Architecture · 架构](#-architecture--架构)
- [Port Allocation · 端口分配](#-port-allocation--端口分配)
- [Endpoints · 端点](#-endpoints--端点)
- [Project Structure · 项目结构](#-project-structure--项目结构)
- [Roadmap · 路线图](#-roadmap--路线图)
- [License](#-license)

---

## 🎯 Why · 为什么

> 🇺🇸 **The Problem:** 15 specialized agent profiles (Chinese imperial governance style) could only communicate via async Kanban cards dropped into a shared board. No real-time capability discovery, no synchronous task handoff — like ministries sending carrier pigeons.

> 🇨🇳 **痛点：** 三省六部 15 个专业 agent profile 之间只能靠 Kanban 卡异步通信。无法实时发现其他部门的能力，无法同步委派任务 — 如同各部之间靠飞鸽传书。

**hermes-a2a** fixes this by implementing [Google's A2A Protocol](https://github.com/a2aproject/A2A) (23.4K ⭐) as a Hermes plugin:

- 🔍 **Capability Discovery** — Each profile exposes an auto-generated Agent Card with skills, tools, and model info
- ⚡ **Synchronous Task Delegation** — POST a task to any ministry's A2A endpoint, get real-time results
- 📡 **Streaming Progress** — SSE endpoint for live task status updates
- 🛡️ **Production-Grade Resilience** — All 6 A2A servers managed by macOS launchd with <1s auto-recovery
- 🔀 **Dual-Mode Execution** — API Server `/v1/runs` (11s) for regent/default; subprocess fallback (10s) for workers
- 🎯 **Zero-Collision Porting** — `sha256(profile) % 300 + 8650` — verified across all 16 profiles

---

## 📊 Live Status · 运行状态

| Profile · 部门 | Port · 端口 | Skills · 技能 | Execution · 执行 |
|:---|:---:|:---:|:---|
| **regent** · 太子 | 8689 | 13 | API Server /v1/runs |
| **default** · 默认 | 8695 | 13 | API Server /v1/runs |
| **engineer** · 工部 | 8668 | 5 | subprocess |
| **shangshu** · 尚书省 | 8676 | 4 | subprocess |
| **gongbu** · 兵部 | 8698 | 4 | subprocess |
| **budget** · 户部 | 8686 | 3 | subprocess |

```
8/8 endpoints healthy ✅  |  6 A2A + 2 API Server
launchd supervised ✅      |  KeepAlive + ThrottleInterval 30s
PORT_RANGE=300 ✅          |  16 profiles · zero collisions
```

> See `docs/tracking.md` for full deployment log and `docs/audits/` for CC agent team audit reports.

---

## 🚀 Quick Start · 快速开始

### Prerequisites · 前置条件

- Hermes Agent installed (`~/.hermes/hermes-agent/`)
- macOS (launchd supervision) or Linux (systemd port)
- Python 3.10+

### One-Cmd Install · 一行安装

```bash
# Clone and seed symlinks for all profiles
git clone https://github.com/Loveacup/hermes-a2a.git ~/code/hermes-a2a && \
  bash ~/code/hermes-a2a/scripts/seed-a2a-symlinks.sh
```

### Launch · 启动

```bash
# Start all 6 A2A servers (macOS launchd)
# Plists live at ~/Library/LaunchAgents/com.hermes.a2a.{profile}.plist
for profile in engineer shangshu budget regent default gongbu; do
  HOME=/Users/$(whoami) launchctl bootstrap gui/501 \
    ~/Library/LaunchAgents/com.hermes.a2a.$profile.plist
done
```

### Verify · 验证

```bash
# Check all 8 endpoints in one shot
bash ~/code/hermes-a2a/scripts/hermes-a2a-doctor.sh

# Sample output:
# ✅ engineer :8668 → 200  skills: 5
# ✅ shangshu :8676 → 200  skills: 4
# ✅ budget   :8686 → 200  skills: 3
# ✅ regent   :8689 → 200  skills: 13
# ✅ default  :8695 → 200  skills: 13
# ✅ gongbu   :8698 → 200  skills: 4
# ✅ ALL HEALTHY
```

### Your First Task · 第一个任务

```bash
# Send a task to regent (太子)
curl -s -X POST http://127.0.0.1:8689/a2a/tasks \
  -H 'Content-Type: application/json' \
  -d '{"id":"hello-world","sessionId":"demo","message":{"role":"user","parts":[{"type":"text","text":"Reply EXACTLY: Hello from A2A!"}]},"acceptedOutputModes":["text/plain"]}'

# Wait ~12s, then check result
sleep 12
curl -s http://127.0.0.1:8689/a2a/tasks/hello-world | python3 -m json.tool
```

Expected output: `status: "completed"`, `artifact.response: "Hello from A2A!"`, `artifact.mode: "api_server"`.

---

## 🏗 Architecture · 架构

```
┌──────────────┐     ┌─────────────────────────────┐
│  Any Client  │────▶│  hermes-a2a HTTP Server      │
│  (curl, SDK, │     │  (per-profile, port-derived) │
│   other A2A) │     │                               │
└──────────────┘     │  GET  /health                 │
                     │  GET  /agent-card.json        │
                     │  POST /a2a/tasks              │
                     │  GET  /a2a/tasks/{id}         │
                     │  GET  /a2a/tasks/{id}/stream  │
                     └──────────┬────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
    ┌─────────────┐   ┌──────────────┐   ┌────────────────┐
    │ API Server  │   │  Subprocess  │   │  Agent Card    │
    │ /v1/runs    │   │  hermes chat │   │  Auto-Generate │
    │ (regent/    │   │  (engineer/  │   │  from profile  │
    │  default)   │   │  shangshu/   │   │  config.yaml   │
    │   ~11s      │   │  budget/     │   │                │
    │             │   │  gongbu)     │   │                │
    │             │   │   ~10s       │   │                │
    └─────────────┘   └──────────────┘   └────────────────┘

Dual-mode execution:
  • API Server mode — POST to regent:8643/v1/runs, poll for result
  • Subprocess mode — spawn `hermes chat -q --profile X` (with .env auth injection)
  • Automatic fallback: API Server failure → subprocess retry

Supervision:
  • All 6 A2A servers managed by macOS launchd
  • KeepAlive + ThrottleInterval 30s — crash → <1s auto-recovery
  • Health aggregator script monitors all 8 endpoints
```

### Agent Card Auto-Generation

Each profile's Agent Card is built **dynamically from its `config.yaml`** — no manual sync needed:

- `toolsets` from config → A2A 1.0 `skills[]` with `id/name/description/examples/tags`
- `model` info from config → `capabilities.model` field
- Profiles with sparse config (workers) show only their declared toolsets
- Profiles without explicit toolsets (regent/default) show full built-in capability set

---

## 🔢 Port Allocation · 端口分配

```
Formula:  sha256(profile_name) % 300 + 8650

Verified: 16 profiles → 16 unique ports, zero collisions ✅
```

| Profile | Port | Profile | Port |
|:---|---:|:---|---:|
| jiangzuojian | 8654 | reviewer | 8661 |
| auditor | 8698 | archivist | 8704 |
| engineer | 8718 | shangshu | 8726 |
| budget | 8736 | default | 8745 |
| tester | 8755 | gongbu | 8798 |
| hanlinyuan | 8802 | dispatcher | 8807 |
| planner | 8828 | registry | 8924 |
| protocol | 8833 | regent | 8839 |

> ⚠️ The 6 deployed profiles use launchd-managed ports (8668–8698) from the legacy PORT_RANGE=50 era. These will be re-aligned to the formula above in a future migration. See ADR-002 in `docs/methodology.md`.

---

## 📡 Endpoints · 端点

| Method | Path | Description · 说明 |
|:---|:---|:---|
| `GET` | `/health` | Health check · 健康检查 |
| `GET` | `/a2a/.well-known/agent-card.json` | A2A 1.0 Agent Card · 能力清单 |
| `POST` | `/a2a/tasks` | Create task · 创建任务 |
| `GET` | `/a2a/tasks/{id}` | Task status + artifact · 任务状态 |
| `GET` | `/a2a/tasks/{id}/stream` | SSE streaming · 流式进度 (WIP) |
| `DELETE` | `/a2a/tasks/{id}` | Delete task · 删除任务 |

---

## 📁 Project Structure · 项目结构

```
hermes-a2a/
├── plugin.py              # Hermes plugin entry
├── server.py              # A2A HTTP server (threading + daemon)
├── agent_card.py          # Auto-generate A2A 1.0 Agent Card
├── task_handler.py        # Dual-mode execution dispatcher
├── plugin.yaml            # Plugin metadata
├── requirements.txt       # pyyaml
├── scripts/
│   ├── hermes-a2a-doctor.sh   # Health aggregator (8/8 endpoints)
│   └── seed-a2a-symlinks.sh   # Per-profile symlink seeding
├── docs/
│   ├── tracking.md            # Project tracking log
│   ├── methodology.md         # ADRs + design decisions
│   ├── architecture-comparison.md  # A2A vs API Bridge analysis
│   ├── deployment-report.md       # Step 1 deployment record
│   └── audits/            # CC agent team audit reports
├── CLAUDE.md              # AI collaboration doc
└── README.md              # You are here
```

---

## 🗺️ Roadmap · 路线图

- [x] **v0.1.0** — Core 4 profile deployment (engineer/shangshu/budget/gongbu)
- [x] **v0.1.1** — P0 fixes: PORT_RANGE=300 · dual-mode execution · launchd full supervision · A2A 1.0 skills · source ↔ deploy sync
- [ ] **v0.2.0** — Full 15-profile deployment with formula-based port allocation
- [ ] **v0.3.0** — Real SSE streaming (current: polling simulation)
- [ ] **v1.0.0** — JSON-RPC 2.0 transport · EmpireThread event bridge · Hindsight memory integration

---

## 📄 License

MIT — see [LICENSE](LICENSE).

## 🙏 Acknowledgements · 致谢

- [Google A2A Protocol](https://github.com/a2aproject/A2A) — Agent-to-Agent communication standard
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — The multi-profile agent platform
- Claude Code agent team — Independent security + architecture audit (see `docs/audits/`)
