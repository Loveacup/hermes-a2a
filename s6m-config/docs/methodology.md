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

### 为什么共享代码 + per-profile symlink 而非纯全局插件？

- 代码一份：`~/.hermes/plugins/hermes-a2a/`（15 profile 共享）
- 部署：per-profile symlink `~/.hermes/profiles/<name>/plugins/hermes-a2a → ~/.hermes/plugins/hermes-a2a/`
- 原因：Hermes plugin 加载机制基于 per-profile `plugins/` 目录；symlink 实现代码共享但 profile 独立激活
- Agent Card 从各 profile 的 config.yaml 自动生成——一个文件搞定所有差异
- 端口自动分配（hash profile name % 200 + 8650），无碰撞
- `scripts/seed-a2a-symlinks.sh` 一键创建所有 profile symlink

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
- EmpireThread P4 需要从零定义 Schema、heartbeat、私有长期记忆直连——最大不确定性
- A2A 是成熟标准，Agent Card + Task + Stream 三件套覆盖全部需求
- API 桥接已验证的 HTTP 模式可直接升级到 A2A

**后果**：
- EmpireThread P4 的心跳 cron 和 capability_snapshot Schema 保留，并入 A2A 的 Agent Card
- 不装 agentmemory；长期记忆走 Supermemory 单层（详见 ADR-005）

### ADR-002：共享代码 + per-profile symlink 部署

**日期**：2026-05-27
**状态**：已修订（原为"全局插件"，实测需 per-profile symlink）

**背景**：需要决定插件安装位置。Hermes plugin 加载机制基于 per-profile `plugins/` 目录。

**决策**：代码存一份在 `~/.hermes/plugins/hermes-a2a/`，每个 profile 通过 symlink 引用。`scripts/seed-a2a-symlinks.sh` 一键创建。

**理由**：
- 减少维护负担（1 份代码 vs 15 份）
- Agent Card 自动从各 profile config 生成差异部分
- 端口碰撞已通过 PORT_RANGE=300 解决（验证 16 profile 零碰撞）
- 各 profile 可独立 enable/disable

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

### ADR-005：EventBridge 长期记忆 sink 由 Hindsight 替换为 Supermemory（单层架构）

**日期**：2026-05-30
**状态**：接受
**决策依据**：ARCH-TEST-001

**背景**：v2 缩窄版 EmpireThread 事件桥设计了两个下游 sink——Obsidian（本地知识库）+ Hindsight（长期语义记忆）。实施验证后发现：

1. **Hindsight 从未启用** — `HINDSIGHT_API_KEY` 从未在生产环境配置，`daemon.default_sinks()` 每次启动都跳过实例化（`daemon.py:28-32`）；`~/.hermes/event-bridge/hindsight/` 目录始终为空（无 `pending.jsonl`、无 `dlq.jsonl`）
2. **三省六部全线已用 Supermemory** — `~/.hermes/supermemory.json` 已配置 API key 与 container_tag 映射（regent → `hermes-cabinet`，default → `hermes`，fallback `hermes`），其他长期记忆通路已全部依赖 Supermemory
3. **双层架构无收益** — 在仅一个 sink 实际生效的情况下保留两套抽象/降级/DLQ 逻辑只增维护成本

**决策**：废除 Hindsight sink，新建 Supermemory sink 作为唯一长期记忆后端；EventBridge 收敛为 `Obsidian + Supermemory` 双 sink 单层架构。

**实现要点**：
- 新 sink：`core/event_bridge/sinks/supermemory.py` + `SupermemorySink(name="supermemory")` + `HttpTransport`（urllib，无 SDK 依赖）
- HTTP：`POST https://api.supermemory.ai/v3/documents`，`Authorization: Bearer $SUPERMEMORY_API_KEY`
- Payload（camelCase）：`content`（渲染为 markdown）/ `containerTags: [tag]` / `customId: event_id` / `metadata: {event_type, profile, timestamp, task_id?}`
- `accept(evt)` 拒绝 `_source=sink_writeback`（防回路，同 Obsidian sink）
- daemon：条件 `if os.environ.get("SUPERMEMORY_API_KEY")`；launchd plist wrap zsh 从 `~/.hermes/.env` 抽取 key
- container_tag 映射：regent → `hermes-cabinet`，default → `hermes`，其他 profile → fallback `hermes`

**与原 Hindsight 设计的关键简化（必须留痕）**：

| 维度 | 原 Hindsight sink | 新 Supermemory sink |
|------|------------------|--------------------|
| 降级机制 | L0 实时(2s) → L1 重试(1/4/16s) → L2 DLQ → L3 熔断 60s | **无降级** — best-effort，失败吞掉记 warning |
| 持久化 | per-sink `pending.jsonl` 强制落盘 + `dlq.jsonl` 死信 | **不入队** — write() 直发 transport |
| Cursor | 失败不推进 cursor，等下次 flush | 失败仍**推进 cursor**（不阻塞主路径） |
| 代码量 | 196 行 | ~80 行 |

**简化理由**：daemon 是 best-effort 二级 sink；Obsidian 是人类可读 source of truth 且本地 IO 无网络抖动；丢一两条 Supermemory 不影响审计链路完整性。若未来观测到丢失率不可接受，再补 pending 队列/DLQ。

**后果**：
- 代码删除：`core/event_bridge/sinks/hindsight.py`（-196 行）
- 测试删除：`tests/unit/test_event_bridge_hindsight.py`（10 用例）
- 测试新增：`tests/unit/test_event_bridge_supermemory.py`（9 用例 S1-S9）
- 当前共 **52 个 event_bridge 测试全绿**
- 部署同步完成，daemon 已重启，观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}`
- 文档全面同步：v2 缩窄版、v1.0 综合设计文档、路线图、step4 调查报告、tracking 等

---

## 部署分层

### Step 1：核心 4 profile（最小闭环）

尚书省作为 API Hub，兵部/工部/户部作为星型节点。验证 A2A 全链路：Agent Card 发现 → Task 委派 → 结果回传。

### Step 2：全量 15 profile

扩展到全部三省六部 profile。引入 JSON-RPC 2.0 标准格式。

### Step 3：EmpireThread 事件桥

MEMORY_QUERY 事件通过 A2A Task 承载，实现跨 profile 长期记忆只读查询。长期记忆后端 = Supermemory（ADR-005），按 profile → container_tag 映射隔离。

---

## 通信模式

```
能力发现:  GET /a2a/.well-known/agent-card.json → {skills, model, toolsets}
任务委派:  POST /a2a/tasks → {id, status: "working"}
进度流:   GET /a2a/tasks/{id}/stream → SSE events
记忆查询:  POST /a2a/tasks {type: "MEMORY_QUERY"} → EmpireThread 事件桥 → Supermemory
```

## 与三省六部现有机制的协作

| 机制 | 职责 | 保留？ |
|------|------|:---:|
| Kanban | 任务审计链、离线降级、跨 session 持久化 | ✅ 保留 |
| A2A | 实时同步通信、能力发现、流式进度 | 🆕 新增 |
| EmpireThread | 事件溯源、上下文标签 | ✅ 保留 |
| Supermemory | 私有长期记忆（按 profile container_tag 隔离） | ✅ 唯一长期记忆后端（ADR-005） |

**A2A 不替代 Kanban**——Kanban 是审计链（谁在何时做了什么），A2A 是通信层（实时问一句话）。两者互补。

---

## 安全边界

- **仅 localhost**：A2A server 绑定 127.0.0.1，不暴露到网络
- **profile 间长期记忆隔离**：Supermemory 按 `container_tag` 切分（regent → `hermes-cabinet`，default/其他 → `hermes`），同 profile 内可读、跨 profile 默认不可见
- **端口隔离**：每个 profile 独立端口，通过 hash 自动分配
- **CORS 开放**：`Access-Control-Allow-Origin: *`（仅 localhost，无安全风险）

---

## 关联文档

- A2A Protocol Spec: https://github.com/a2aproject/A2A
- 三省六部宪章: `three-provinces-constitution` skill
- 项目追踪: `docs/tracking.md`
- 内阁群配置: Telegram chat -5133970461
