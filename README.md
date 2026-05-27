# hermes-a2a

Hermes Agent 的 A2A (Agent-to-Agent) 协议实现。基于 [Google A2A Protocol](https://github.com/a2aproject/A2A) (Apache 2.0)，为 Hermes 多 profile 系统提供实时跨 profile 通信：能力自动发现、同步任务委派、SSE 流式响应。

## 这是一个 monorepo

```
hermes-a2a/
├── core/             # 🔧 通用 A2A 内核（任何 Hermes 用户都能用，无业务依赖）
│   ├── plugin.py / server.py / agent_card.py / task_handler.py
│   ├── plugin.yaml / requirements.txt
│   ├── templates/   ← launchd plist 模板（带 {{占位符}}）
│   ├── scripts/     ← 健康聚合 + symlink 种子
│   └── README.md    ← 内核文档
│
├── s6m-config/       # 🏯 三省六部治理体系专属配置（与 core/ 分离）
│   ├── plists/      ← 16 个具体 launchd plist
│   ├── docs/        ← ADR 方法论 / 部署报告 / 审计
│   ├── port-map.md  ← 16 profile 端口快查
│   └── README.md    ← 三省六部部署文档
│
├── README.md         ← 你正在读的这个
└── CLAUDE.md         ← AI 协作文档（架构图 + 工作流）
```

## 为什么拆 core 和 s6m-config

- **core/** 只包含协议代码——任何用 Hermes 的人都能 fork 或 vendor，不用关心三省六部
- **s6m-config/** 把「16 个 profile 名、端口号、部门职责」这种业务特定信息隔离出来
- 想给别的体系用？只需要复制 `s6m-config/` 那一层，自己写 plist + port-map，`core/` 不动
- 这也让上游审计跟下游部署的演进节奏可以分开走

## 快速上手

### 三省六部 16-profile 部署（如果你就是这个体系的运维）
- 读 [s6m-config/README.md](s6m-config/README.md)
- 完整步骤、端口表、踩坑总结都在那

### 一般 Hermes 用户接入 A2A
- 读 [core/README.md](core/README.md)
- 把 `core/` 拷到你的 `~/.hermes/plugins/hermes-a2a/`，按需写自己的 plist

## 当前状态

- 16/16 A2A 端点 + 2/2 API Server 健康（`bash core/scripts/hermes-a2a-doctor.sh` 自检）
- launchd KeepAlive 监管，崩溃 ~1s 内复活
- 双模 task 执行：regent / default 走 Hermes 原生 `/v1/runs`；其他 profile 走 `hermes chat -q` subprocess
- A2A 1.0 spec 合规（id / name / description / examples / tags 全字段）
- 审计历史见 [s6m-config/docs/audits/](s6m-config/docs/audits/)

## 协议端点（每个 profile 一份）

- `GET /health`
- `GET /a2a/.well-known/agent-card.json`
- `POST /a2a/tasks`（异步执行，立即返 task id）
- `GET /a2a/tasks/{id}`（查询状态 + artifact）
- `GET /a2a/tasks/{id}/stream`（SSE，当前 stub）

## 体系角色

hermes-a2a 在三省六部治理体系中连接以下关键角色：

| 角色 | Profile | A2A 端口 | 身份 |
|------|---------|----------|------|
| **监国太子** | `regent` | 8939 | 三省六部总枢，承旨、拟制、派工、稽核 |
| **小黄** | `default` | 8945 | Alex 的个人助理，**独立于三省六部体系之外** |
| 六部/三省/御史台等 | 14 个 profile | 详见 port-map | 三省六部各职能部门 |

> **小黄的身份**：小黄（default profile）是 Alex 的贴身秘书，不属于三省六部任何部门。
> 他与太子（regent）是**平等协作**关系，非上下级。在 A2A 讨论（内阁群 ROLEPLAY/SYNTHESIZE）中，
> 小黄以独立视角提供分析，不代三省六部发言。落款统一为【小黄】。

## 关联文档

- [core/README.md](core/README.md) — 内核插件文档
- [s6m-config/README.md](s6m-config/README.md) — 三省六部部署
- [s6m-config/docs/methodology.md](s6m-config/docs/methodology.md) — ADR 方法论
- [s6m-config/docs/s6m-a2a-optimization.md](s6m-config/docs/s6m-a2a-optimization.md) — 体系优化方案
- [s6m-config/docs/audits/](s6m-config/docs/audits/) — 审计报告
- [CLAUDE.md](CLAUDE.md) — AI 协作 + 工作流

## License

Apache-2.0
