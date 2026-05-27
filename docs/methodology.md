# hermes-a2a 方法论文档

## 设计哲学

### 为什么 A2A？

三省六部 15 个 profile 之间的通信，当前仅靠 Kanban 卡——异步投递 + 60s 轮询。这无法满足以下需求：

1. **同步查询**："刑部，这个 commit 是否符合安全规范？"——需要秒级响应
2. **能力发现**："当前谁能处理 JSON Schema 验证？"——需要动态感知
3. **实时进度**：长任务执行中，不想每 60s 轮询一次

候选方案对比：

| 方案 | 同步 | 能力发现 | 标准化 | 代码量 | 依赖 |
|------|:---:|:---:|:---:|------|------|
| Kanban 轮询（现状） | ❌ | ❌ | ✅ | 0 | 0 |
| 原始 HTTP API | ✅ | ❌ | ❌ | 高 | 0 |
| **A2A Protocol** | ✅ | ✅ | ✅ | 中 | a2a-sdk |
| agentmemory 共享层 | 部分 | ❌ | 部分 | 高 | agentmemory |
| EmpireThread P4 自建 | ✅ | 部分 | ❌ | 最高 | 0 |

**A2A 胜出的关键原因**：
- 是 Google → Linux Foundation 的开放标准（23.4K⭐），不是自造轮子
- Agent Card 解决了"profile 能做什么"的动态感知问题
- HTTP/JSON 传输层与内阁 API 桥接基础完全兼容
- Python SDK 一行安装，无需额外基础设施

### 为什么全局插件而非 per-profile？

- 15 个 profile 各装一份 = 15 份代码维护。全局一份 = 1 份。
- Agent Card 从各 profile 的 config.yaml 自动生成——一个文件搞定所有差异
- 端口自动分配（hash profile name），无冲突

### 为什么 HTTP/JSON 先行，JSON-RPC 后补？

- A2A 规范同时支持 HTTP+JSON/REST 和 JSON-RPC 2.0
- 当前内阁 API 桥接已验证 HTTP+JSON 可行
- JSON-RPC 的标准任务生命周期管理在 Step 2 引入

---

## 架构决策记录（ADR）

### ADR-001：选择 A2A 作为跨 profile 通信主干

**日期**：2026-05-27
**状态**：接受

**背景**：内阁 API 桥接（default↔regent localStorage HTTP）验证了同步跨 profile 通信的可行性。需要从 2 个 profile 的点对点扩展到 15 个 profile 的 mesh。

**决策**：采用 A2A Protocol 作为跨 profile 通信主干，替代原计划的 EmpireThread Phase 4（自建 MEMORY_QUERY 协议）。

**理由**：
- EmpireThread P4 需要从零定义 Schema、heartbeat、Hindsight 直连——最大不确定性
- A2A 是成熟标准，Agent Card + Task + Stream 三件套覆盖全部需求
- API 桥接已验证的 HTTP 模式可直接升级到 A2A

**后果**：
- EmpireThread P4 的心跳 cron 和 capability_snapshot Schema 保留，并入 A2A 的 Agent Card
- 不装 agentmemory，不 merge Hindsight bank

### ADR-002：全局插件而非 per-profile 部署

**日期**：2026-05-27
**状态**：接受

**背景**：需要决定插件安装位置。

**决策**：安装到 `~/.hermes/plugins/hermes-a2a/`（全局），所有 profile 共享同一份代码。

**理由**：
- 减少维护负担（1 份 vs 15 份）
- Agent Card 自动从各 profile config 生成差异部分
- 端口冲突通过 hash(profile_name) 自动分配

### ADR-003：HTTP/JSON 先行，JSON-RPC 2.0 后补

**日期**：2026-05-27
**状态**：接受

**决策**：Step 1 使用纯 HTTP/JSON REST 端点；Step 2 引入 A2A 标准 JSON-RPC 2.0 格式。

**理由**：先跑通核心链路（health + Agent Card + Task CRUD），再标准化格式。降低初期复杂度。

### ADR-004：Agent Card 从 profile config 自动生成

**日期**：2026-05-27
**状态**：接受

**决策**：Agent Card 的 skills/model/toolsets 字段从各 profile 的 config.yaml 自动提取，不手动维护。

**理由**：手动维护 15 份 Agent Card 必然过期。自动生成保证内容与 profile 实际配置一致。

---

## 部署分层

### Step 1：核心 4 profile（最小闭环）

尚书省作为 API Hub，兵部/工部/户部作为星型节点。验证 A2A 全链路：Agent Card 发现 → Task 委派 → 结果回传。

### Step 2：全量 15 profile

扩展到全部三省六部 profile。引入 JSON-RPC 2.0 标准格式。

### Step 3：EmpireThread 事件桥

MEMORY_QUERY 事件通过 A2A Task 承载，实现跨 profile Hindsight 只读查询。不 merge bank。

---

## 通信模式

```
能力发现:  GET /a2a/.well-known/agent-card.json → {skills, model, toolsets}
任务委派:  POST /a2a/tasks → {id, status: "working"}
进度流:   GET /a2a/tasks/{id}/stream → SSE events
记忆查询:  POST /a2a/tasks {type: "MEMORY_QUERY"} → EmpireThread 事件桥
```

## 与三省六部现有机制的协作

| 机制 | 职责 | 保留？ |
|------|------|:---:|
| Kanban | 任务审计链、离线降级、跨 session 持久化 | ✅ 保留 |
| A2A | 实时同步通信、能力发现、流式进度 | 🆕 新增 |
| EmpireThread | 事件溯源、上下文标签 | ✅ 保留 |
| Hindsight | 私有长期记忆 | ✅ 不 merge |

**A2A 不替代 Kanban**——Kanban 是审计链（谁在何时做了什么），A2A 是通信层（实时问一句话）。两者互补。

---

## 安全边界

- **仅 localhost**：A2A server 绑定 127.0.0.1，不暴露到网络
- **profile 间互不可见 Hindsight**：MEMORY_QUERY 走只读查询协议，不 merge bank
- **端口隔离**：每个 profile 独立端口，通过 hash 自动分配
- **CORS 开放**：`Access-Control-Allow-Origin: *`（仅 localhost，无安全风险）

---

## 关联文档

- A2A Protocol Spec: https://github.com/a2aproject/A2A
- 三省六部宪章: `three-provinces-constitution` skill
- 项目追踪: `docs/tracking.md`
- 内阁群配置: Telegram chat -5133970461
