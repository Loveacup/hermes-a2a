# A2A Plugin vs API Bridge — 对比分析（Part 1）

## Spawn 配置
- 2 个 Explore agent 并行后台跑：
  - agent #1 — A2A 端口 co-location 可行性（端口预算、plugin lifecycle hook、aiohttp/stdlib HTTPServer 差异）
  - agent #2 — /health 增强 + /v1/capabilities 字段对照 + 融合路线图

## 一、A2A 挂载到 API Server 端口 vs 独立端口

### 可行性（agent #1 找到的具体接入点）
- API Server 在 `gateway/platforms/api_server.py:3488` 通过 `web.Application(middlewares=mws,...)` 构造 aiohttp app，随后 add_get/add_post 挂路由
- **当前 PluginContext 没有任何 route 注册 hook**（hermes_cli/plugins.py:287-465 暴露 register_tool / register_hook / register_skill 等，但无 register_aiohttp_route）
- 要实现 co-location，需新增 hook：在 `VALID_HOOKS` (plugins.py:128) 加 `on_api_server_init`，并在 `APIServerAdapter.connect` 构造完 app 后 fire（约 plugins.py:3515 位置）
- 同时要把 A2A handler 从 `BaseHTTPRequestHandler`（同步）改成 `async def`（aiohttp），约 +200 LOC 改动

### Co-location 的好处
- 端口预算减半：15 profile 从 30 端口（API Server + A2A）降到 15
- 单一 auth/CORS surface（HERMES_API_SERVER_KEY、CORS origin 一次配置生效）
- 生命周期统一：gateway 退出即两个 API 都退出，无 orphan subprocess
- 服务发现统一：`/v1/capabilities` 可声明 `a2a_tasks: true` 与 `/a2a/*` 端点
- 直接 in-process 访问 gateway 已加载的 config / state.db / session store，agent_card.py 不再每次磁盘读 yaml + SOUL.md

### Co-location 的坏处
- 故障隔离消失：A2A 一个 bad request 把整个 gateway 拖崩 → Telegram/Discord/WhatsApp 全断
- aiohttp 依赖污染：当前 server.py 纯 stdlib 可移植；改 aiohttp 后 A2A 与 gateway 同生共死
- 热重启代价：改 A2A 代码要重启整个 gateway，不能像现在 `kill <server.py-pid>` 单点重启
- 调试更复杂：sync handler 改成 async + aiohttp 中间件栈（CORS、body limit、security headers）后排错难度↑

### agent #1 推荐 + 主 agent 同意
- **Step 2 维持独立端口** —— 至少在 A2A 还是「实验性」时不要 co-locate
- 何时回头看：profile 数 ≥ 50（端口紧张）/ A2A 成为 critical path / 想做统一 auth & 监控
- 真要做 co-location 的话，4 步路径：(1) `VALID_HOOKS` 加 `on_api_server_init`；(2) `PluginContext.register_aiohttp_routes`；(3) `APIServerAdapter.connect:3515` fire 这个 hook；(4) 把 A2A handler 改成 async

## 二、/v1/capabilities vs A2A Agent Card 字段对照

### Hermes /v1/capabilities 现有字段（api_server.py:1048-1105）
- object / platform / model（字符串）/ auth (type, required)
- runtime (mode, tool_execution, split_runtime, description)
- features (一堆 boolean：chat_completions、responses_api、run_submission、run_events_sse、approval_events、…)
- endpoints (各 /v1/* 端点的 method+path 映射)

### A2A Agent Card 字段（agent_card.py:14-33）
- name (带 profile 名)
- description (取 SOUL.md 首行非注释)
- url (A2A endpoint root)
- provider (organization + URL)
- capabilities (streaming, pushNotifications)
- defaultInputModes / defaultOutputModes（["text", "file"]）
- skills (array of {id, description}，按 SKILL_MAP 从 toolsets 投影)
- currentModel ({default, provider})
- version + protocolVersion ("1.0" A2A 规范版本)

### 主要 gap（A2A 有而 Hermes 没有的）
- **profile 感知**：/v1/capabilities 完全不知道自己运行在哪个 profile
- **skills 数组**：Hermes 内部有 toolset 概念但不暴露 A2A 规范的 normalized skill id
- **input/output modes**：Hermes 不声明输入/输出模态
- **provider 元数据**：A2A 带 GitHub URL，Hermes 没有
- **protocolVersion**：Hermes 没把 A2A 规范版本 surface 出来

### /v1/capabilities 增强后能否当 Agent Card 用？
- 可以——补齐 name / provider / defaultInputModes / defaultOutputModes / protocolVersion / skills，把 model 字段从字符串改成 `{default, provider}` 对象
- 然后 `/a2a/.well-known/agent-card.json` 就能 alias 到 `/v1/capabilities`，无需独立端点

## 三、/health 增强方案

### 现状
- `/health` (api_server.py:1002)：`{"status":"ok","platform":"hermes-agent"}` —— 极简
- `/health/detailed` (api_server.py:1006)：gateway 状态、已连平台、PID、uptime —— **不含 agent 能力 / 模型信息**
- A2A `/health`：`{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":<X>}` —— 多了 service + version + profile

### 建议
- 把 A2A 的 profile + model + capabilities 字段 backport 到 `/health/detailed`，让客户端单端点就能完成 A2A discovery
- 进一步：单独加个 `/v1/agent-card`（A2A spec 形态），调用方按需选

## 四、Skill Map 评估
- 当前 SKILL_MAP（agent_card.py:7-12）：terminal→shell-execution / file→file-operations / web→web-research / browser→browser-automation / kanban→kanban-workflow / memory→persistent-memory / vision→image-analysis / image_gen→image-generation / code_execution→code-execution / session_search→session-search / cronjob→scheduled-tasks / delegation→task-delegation
- **lossy 但合理**：A2A skill id 是规范化对外契约，Hermes toolset 是内部插件命名，二者本来就要做翻译
- 没覆盖到的 Hermes 工具（approval / mcp / compression 等）暂时不映射是对的，因为 A2A 规范也没定义它们

## 五、/v1/runs vs /a2a/tasks 形态对比

### Hermes /v1/runs（api_server.py:2952-3231）
- 输入：`{input, instructions, conversation_history, previous_response_id, model, session_id}`
- 输出：`{object:"hermes.run", run_id, status, created_at, updated_at, session_id, model, ...}`
- 状态存：in-memory dict + asyncio.Queue（SSE）；~300s idle TTL
- 事件 SSE：`/v1/runs/{run_id}/events`，事件类型丰富（message.delta / tool.call / tool.result / approval.request）
- 支持原生 approval 流（`/v1/runs/{id}/approval`）

### A2A /a2a/tasks（server.py + task_handler.py）
- 输入：`{id, context_id, message}`
- 输出：`{id, status, context_id, message, created_at, history, ...}`
- 状态存：in-memory dict，无 TTL
- 执行：`subprocess.run(["hermes","chat","-q",...])` —— 同步阻塞，无流式
- 事件 SSE：`/a2a/tasks/{id}/stream` —— 当前只 echo 静态 task 后 `[DONE]`，**不是真流式**
- 无 approval 概念

### 结论
- 形态相似（都是 task/run + SSE），**语义差距大**：Hermes 是 event-driven async；A2A 当前是 fire-and-forget subprocess
- /v1/runs 是 /a2a/tasks 的超集，可承接所有 A2A 任务（A2A 改成 thin wrapper）

## 六、融合路线图（agent #2 推荐 + 主 agent 同意）

### Option A — 维持两套（现状）
- pro：零风险、可独立演进、debug 简单
- con：重复 health、重复 task store、用户混淆「该用哪个端点」

### Option B — A2A 改造成 /v1/runs + /v1/capabilities 之上的 thin adapter
- A2A 仍占独立端口，但内部把 POST /a2a/tasks 翻译成 HTTP POST /v1/runs，/a2a/.well-known/agent-card.json 调 /v1/capabilities 然后包成 A2A schema
- pro：单一 task store；session 连续性共享；A2A 变薄；不动 Hermes 核心
- con：A2A 失去自主演进余地；HTTP 间多一跳延迟；迁移期 task_id/run_id 映射逻辑要维护

### Option C — 干脆 deprecate hermes-a2a，把 A2A spec 推到 Hermes 上游
- /v1/capabilities 增强成 A2A 兼容；Hermes 直接 serve `/a2a/.well-known/agent-card.json`
- pro：单一代码库；A2A 变 Hermes 公共契约；上游团队维护
- con：依赖上游接受 PR；现有 A2A 集成可能 breaking；上游可能不想把 A2A 当核心依赖

### 推荐路径：B → C 两阶段
- **Phase 1（Option B）**：refactor hermes-a2a 为 /v1/* 的适配层，验证规范一致性，不动 Hermes 核心
- **Phase 2（Option C）**：Phase 1 稳定后向上游提 PR 把 A2A 折进 Hermes core
- 这样先消除重复后端、不阻塞上游协调

## 七、Step 2 收敛动作清单（按顺序，每步一个 PR 级改动）

1. **扩展 /v1/capabilities**（Hermes 上游 PR，api_server.py:1048-1105）—— 加 name / description / profile / provider / defaultInputModes / defaultOutputModes / skills 数组 / protocolVersion，model 字段对象化
2. **扩展 /health/detailed**（Hermes 上游 PR，api_server.py:1006）—— 加 profile / model 对象 / capabilities / version
3. **refactor hermes-a2a server.py 为 adapter**（hermes-a2a PR）—— `/a2a/.well-known/agent-card.json` 转代理 /v1/capabilities；`/a2a/tasks` 转代理 /v1/runs；`/a2a/tasks/{id}/stream` 转代理 /v1/runs/{id}/events
4. **task schema 翻译层**（hermes-a2a PR，新文件 task_adapter.py）—— context_id ↔ session_id、a2a 字段 ↔ run 字段
5. **替换 task_handler.py 的 subprocess 调用**（hermes-a2a PR）—— 改成 HTTP POST /v1/runs + SSE 等结果
6. **A2A spec conformance test**（hermes-a2a PR，新增 tests/）—— 验证 agent-card schema、task create→run create 翻译、event stream 格式
7. **(可选 Phase 2)**：上游 Hermes 直接 serve A2A schema，hermes-a2a 标记 deprecated

## TL;DR（更新：含全链路终测）
- 最大 gap：Hermes /v1/capabilities 没有 profile / skills / input-output modes / protocolVersion；/health 没有 model 与能力
- 推荐方向：Phase 1 = hermes-a2a 改造成 /v1/* 之上的 adapter（独立端口保留以隔离故障）；Phase 2 = 推到 Hermes 上游
- 单一最有用的下一步：扩展 /v1/capabilities 加入 profile / skills / currentModel 对象 / protocolVersion=1.0
- Step 2 不做 co-location：A2A 还实验中，需要故障隔离；plugin hook 也得先在 Hermes 加 `on_api_server_init`
- **全链路终测：12/12 全部通过**（含 6 端点 health + 6 端点 Agent Card + 双向跨协议 + 跨协议 task）
