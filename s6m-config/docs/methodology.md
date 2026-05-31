---
status: 树苗
type: 方法论
tags: [type/方法论, ai/hermes, 三省六部, six-ministries, a2a]
created: 2026-05-31
version: v2.0
---

# 三省六部 × A2A 方法论文档 v2.0

> 本文档基于 [[20_实施]] 下设计、追踪、测试、排障、ADR 与 [[10_制度]] 决策记录综合提炼，是 Hermes 多 Agent 治理体系自 2026-05-19 到 2026-05-31 的工程方法论快照。

---

## 一、设计哲学

### 1.1 制度先行：分权而非堆 Agent

「监国三省六部制 Agent 架构」（Regent 3S6M Agent Architecture）的第一原则是 **分权而非堆 Agent**：多 Agent 系统的真问题不是「有多少专家」，而是**谁能拟制、谁能审核、谁能执行、谁负责追责**。

唐代制度被映射为四类角色：

- **三省**：流程权力（拟 / 审 / 派）
- **六部**：治理职能（人 / 财 / 礼 / 运 / 法 / 工）
- **御史台 / 史馆**：监察与归档，独立于执行链
- **监国**：统筹但重大事项请示用户（父皇）

> 用户授权，监国统筹；中书拟案，门下封驳，尚书派工；六部分职，御史监察，史馆留痕。

七条切分原则贯穿全部子系统：

> 中书能想不能干、门下能驳不能干、尚书能派不能改目标、六部能办不能越权、御史能查不参与执行、史馆能记不制造事实、监国能统筹但重大事项请示用户。

### 1.2 结构化任务书取代共享上下文

各 Agent **不共享膨胀上下文**，每次派工通过 YAML 任务书传递必要信息：`task_id / objective / inputs / constraints / acceptance_criteria / timeout / budget / permissions`。缺字段门下省必须打回。

理由：共享上下文会持续膨胀，token 成本指数上升，且无法形成可审计 / 可追责的派工记录；结构化任务书可被门下 / 御史 / 史馆复用。

### 1.3 先星型，后有限互通

初期所有专家走「监国 / 尚书 → 专家 → 回报 → 门下 / 御史复核」的星型结构，系统稳定后才允许有限 A2A 横向通信。全 mesh 会让责任归属和调用链不可追踪；星型先建立可审计基线，再逐步开放横向通信。

### 1.4 16 Profile 差异化辩论而非单体

单一 agent 看同篇文档得同结论（伪讨论）会过早收敛到多数噪声。16 profile 配差异化 skill / 人设 / 工具，从不同角度挑战议题（M2CL 论文验证 20-50% 性能提升），辩论后由太子仲裁（定向干预）才能真正产生深度。

### 1.5 显式 allowlist 而非 blocklist

来自 2026-05-30 Supermemory 双池审计的方法论沉淀：

> 正则安全设计：sanitize 函数应显式 allowlist，而非 blocklist。`[^a-zA-Z0-9_]` 是 deny-by-default 的经典反模式。

`_sanitize_tag` 因为忽略连字符，把 `hermes-cabinet` 切成 `hermes_cabinet`，导致 218 篇文档与 5 篇文档分裂在两个池。

### 1.6 讨论与执行不应割裂

「看板卡 = 协作最小单元」：卡上同时承载讨论线程（agent 自主讨论）和状态流转（执行），讨论和执行是交替的——翰林院发现线索 → 工部排查 → 太子裁决 → 小黄执行，不应分成讨论层 + 执行层两层。

---

## 二、体系架构

### 2.1 全景架构

```
                    用户（父皇）
                              │
                       监国太子（regent）
                              │
        ┌─────────────────────┼─────────────────────┐
        │ 中书省 planner       门下省 reviewer       尚书省 dispatcher
        │ 拟制（方案层）        审核（封驳层）         调度（执行层）
        └─────────────────────┴─────────────────────┘
                              │
        ┌───┬───┬───┬───┬───┬─┴─┬───────┬───────┬───────┐
        吏  户  礼  兵  刑   工   御史台   史馆    翰林院  将作监
       registry budget protocol emergency security engineer auditor archivist hanlinyuan jiangzuojian
```

旁挂：

- **独立角色 default（小黄）**：用户的个人主频道助手，独立于三省六部之外
- **可选扩展**：太常寺（仪式感）、教坊司（娱乐）、tester（司验院/独立测试）

### 2.2 16 Profile 角色表

| # | 部 / 机构 | Profile | 模型 | A2A 端口 | API 端口 | Skills |
|---|----------|---------|------|---------|---------|--------|
| 1 | 监国 | regent | deepseek-v4-pro | 8939 | 8417 | 13 |
| 2 | 中书省 | planner | kimi-k2.6 (moonshot) | 8728 | 8474 | 2 |
| 3 | 门下省 | reviewer | deepseek-v4-pro | 8761 | 8493 | 2 |
| 4 | 尚书省 | dispatcher | deepseek-v4-flash | 8707 | 8465 | 4 |
| 5 | 尚书省 | shangshu | deepseek-v4-flash | 8826 | 8492 | 4 |
| 6 | 吏部 | registry | deepseek-v4-flash | 8928 | 8438 | 3 |
| 7 | 户部 | budget | deepseek-v4-flash | 8936 | 8445 | 3 |
| 8 | 礼部 | protocol | deepseek-v4-flash | 8833 | 8443 | 3 |
| 9 | 兵部 / 工部 | engineer | deepseek-v4-flash | 8718 | 8482 | 5 |
| 10 | 刑部 | security / auditor | deepseek-v4-pro | 8698 | 8468 | 3 |
| 11 | 工部 | gongbu | deepseek-v4-flash | 8898 | 8458 | 4 |
| 12 | 史馆 | archivist | deepseek-v4-flash | 8804 | 8431 | 2 |
| 13 | 司验院 | tester | deepseek-v4-flash | 8755 | 8480 | 4 |
| 14 | 将作监 | jiangzuojian | deepseek-v4-flash | 8654 | 8425 | 5 |
| 15 | 翰林院 | hanlinyuan | gemini-2.5-pro | 8702 | 8466 | 6 |
| 16 | 主频道 | default | deepseek-v4-pro | 8945 | 8460 | 13 |

> 说明：launchd 监管 16 个 `com.hermes.a2a.<profile>.plist`，其中 15 个跑 `server.py`、1 个（registry/吏部）跑 `gateway.py`；`a2a-registry.json` 因此只有 15 条记录（设计意图）。

### 2.3 MCP / A2A / Kanban 三层分工

| 层 | 角色 | 说明 |
|----|------|------|
| MCP | 工具层 | Agent 调工具 / API / 文件系统 |
| A2A | 协作层 | Agent 之间传任务 / 状态 / 交接 / 审查 / 结果 |
| Kanban | 状态层 | 任务卡 / 依赖 / claim / 状态机 |
| Obsidian / qmd | 归档层 | 人类可读的 source of truth |
| Hermes Profile / Skill | 能力与人格层 | profile 配差异化 skill / 人设 / 工具 |

### 2.4 尚书省三层能力模型

来自 [[尚书省升级方案]]：

```
尚书省 = 智能派发层 + 主动协调层 + 汇总呈报层
         │              │              │
         ▼              ▼              ▼
    任务解析→选部    状态监控→恢复    fan-in→合成
    拆卡→派工        阻塞→升级        报告→呈太子
```

### 2.5 记忆架构（ADR-005 后 Supermemory 单层）

v2.0 由「Hindsight L1 + Supermemory L2」双层收敛为 **Supermemory 单层**：所有记忆统一存入云端 Supermemory，本地 LRU 缓存仅作读加速与离线降级，不作为存储真值。

物理隔离用两个 container_tag：

- `hermes`：default 小黄私域
- `hermes-cabinet`：regent + 14 个三省六部 profile 共享
- `hermes-audit`：双池模型外的第三个独立池，仅 auditor + archivist 可读

### 2.6 EmpireThread 事件桥

采用 **Event Sourcing + Sink 插件架构**：

```
pre_tool_call hook → JSONL → launchd sidecar daemon
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                     MEMORY.md     Obsidian      Supermemory
                                                 （ADR-005 后唯一长期 sink）
```

设计原则：不改 Hermes core、Sink 插件化、异步 out-of-band、错误隔离、幂等性（event_id 去重 + cursor 增量）。

---

## 三、部署拓扑

### 3.1 物理拓扑

| 机器 | 角色 |
|------|------|
| Mac mini | Docker + Hermes 16-profile 三省六部常驻 · IP 192.168.x.x · 用户 user |
| MacBook Pro | Claude Code 重活 + 本地小模型部署 |
| Windows 台式 | 游戏 + pi 轻量工作 · SSH 免密已通 Mac mini |

### 3.2 端口公式

```
A2A 端口  = 8650 + sha256(profile) % 300         # PORT_RANGE=300，零碰撞
API 端口  = 8400 + sha256('api:' + profile) % 100 # salted，零碰撞
```

PORT_RANGE 三次迭代 50 → 200 → 300 才彻底消除碰撞。

### 3.3 关键目录树

```
~/.hermes/
├── .a2a-token              ← 共享认证令牌（43 bytes，所有 profile 共用）
├── a2a-registry.json       ← 端口注册表（15 profiles，fcntl 锁 + JSON）
├── supermemory.json        ← 全局配置（全 profile 共享，真实 HOME 下）
├── kanban.db               ← Kanban DB（WAL，6 张表）
├── profiles/<profile>.yaml ← per-profile 配置
├── cache/<profile>.lmdb    ← LRU 缓存（200MB 上限）
├── buffer/<profile>.jsonl  ← 离线写入缓冲（≤1000 条）
├── audit/cross-pool/YYYY-MM-DD.jsonl ← 跨池审计（90 天）
├── secrets.local           ← 敏感凭据逃生通道（不进云端）
└── plugins/hermes-a2a/     ← 部署目录（per-profile symlink 指向 ~/code/hermes-a2a/core/）
    ├── server.py
    ├── auth.py
    ├── registry.py
    ├── paths.py
    ├── task_handler.py
    ├── discuss.py
    ├── a2a_dispatch.py
    ├── agent_card.py
    └── scripts/
        ├── hermes-a2a-doctor.sh
        └── seed-a2a-symlinks.sh
```

源码仓库：`~/code/hermes-a2a/`，已拆分为 `core/`（通用 A2A 内核）+ `s6m-config/`（三省六部专属配置）。

### 3.4 Kanban DB 架构

```
~/.hermes/kanban.db (WAL 模式，6 张表)
├── tasks         — 核心任务表（约 30 字段）
├── task_links    — 父子依赖（parent_id → child_id）
├── task_comments — 评论线程（id, task_id, author, body, created_at）
├── task_events   — 事件日志（30 种事件类型）
├── task_runs     — 每次执行尝试的完整记录
└── kanban_notify_subs — Gateway 通知订阅

# Boards 多项目隔离
~/.hermes/kanban/
├── kanban.db               ← default board
└── boards/<slug>/          ← 每个项目独立 DB
```

### 3.5 launchd 监管

每 profile 一个 `com.hermes.a2a.<profile>.plist`，`KeepAlive=true`，`ThrottleInterval=30`。Gateway 三机：regent（8417）+ default（8460）+ cron-worker（8461）全部由 launchd 监管。

```xml
<!-- 必须用 venv python 而非 system python 3.9 -->
<key>ProgramArguments</key>
<array>
  <string>~/.hermes/hermes-agent/venv/bin/python3</string>
  <string>~/.hermes/plugins/hermes-a2a/server.py</string>
</array>
```

System Python 3.9 不能处理中文 Unicode → A2A server 反复崩溃重启 → 15+ 僵尸进程；必须用 venv Python 3.11。

---

## 四、通信协议

### 4.1 A2A 5 端点规范

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/a2a/.well-known/agent-card.json` | Agent Card（能力发现） |
| POST | `/a2a/tasks` | 创建任务 |
| GET | `/a2a/tasks/{id}` | 任务状态 |
| GET | `/a2a/tasks/{id}/stream` | SSE 流式进度 |

传输层：HTTP/JSON（未来计划 JSON-RPC 2.0）。A2A server 绑定 `127.0.0.1`，仅 localhost。

### 4.2 A2A 调用三步

```bash
# ① 端口发现
TOKEN=$(cat ~/.hermes/.a2a-token)
PORT=$(python ~/.hermes/plugins/hermes-a2a/registry.py port <profile>)

# ② 提交任务
curl -X POST http://127.0.0.1:$PORT/a2a/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"id":"<tid>","task":"..."}'

# ③ 轮询结果
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/a2a/tasks/<tid>
```

### 4.3 两种执行模式

| 模式 | 适用 profile | 启动方式 | 延迟 | MemoryProvider |
|------|------------|---------|------|----------------|
| API Server | regent / default（早期，现已全 16 切到此模式） | 复用 Hermes API Server 长连接 | 3-5s | 加载 |
| Subprocess | 其余 14 profile（已弃用） | spawn `hermes chat -q` | 8-12s | 不加载 |

> 全 16 profile 已切换为 api_server 模式（commit `afa7368`），冷启动开销显著下降。

### 4.4 任务书 YAML Schema

```yaml
task_id: T-20260518-001
from: 尚书省
to: 工部.code-expert
type: implementation
objective: 实现 A2A Agent Card endpoint
context_refs:
  - obsidian://...
  - git://...
inputs:
  requirements: ...
constraints:
  forbidden_paths: [.env, prod-config/]
acceptance_criteria:
  - 测试通过
  - 输出 diff summary
timeout: 30m
budget: medium
permissions:
  filesystem: limited
  network: allowed
  can_spawn_subagents: false
```

### 4.5 handoff_schema v2（严格超集）

```yaml
# v2 = v1 严格超集，仅追加 3 字段
state.recovery_count: int        # 恢复次数
state.last_recovery_reason: str  # 上次恢复原因
delivery_required: bool          # 是否需 watchdog Delivery Bridge
```

> v1 所有字段名 / 位置 / 语义保持不变，向后兼容承诺。

### 4.6 讨论模式

`discuss.py` 仅两种模式（旧版 COMBINED 已修正去除）：

- **ROLEPLAY**：regent ↔ default 2-3 轮辩论，~160s（冷启动惩罚 10×）
- **SYNTHESIZE**：综合多方观点单 agent 产出研判报告，~60-90s

```bash
python ~/code/hermes-a2a/core/discuss.py --mode roleplay --topic "..." --rounds 2
python ~/code/hermes-a2a/core/discuss.py --mode synthesize --topic "..." --depth deep
```

### 4.7 DCI 14 Kind 路由表

| Kind | 路由目标 |
|------|---------|
| PROPOSE | 当前 agent |
| ASK | hanlinyuan |
| EVIDENCE_FOR | 当前 agent（e2e: archivist） |
| EVIDENCE_AGAINST | 当前 agent |
| CHALLENGE | regent（太子仲裁） |
| CLARIFY | 原始发言 agent |
| REFINE | 当前 agent |
| CONCEDE | —（deadlock guard 自动触发） |
| SYNTHESIZE | regent |
| SUMMARIZE | regent |
| META_DIRECTIVE | regent |
| VOTE_FOR / VOTE_AGAINST / ABSTAIN | —（聚合） |

### 4.8 Kanban 状态机（9 态）

```
triage → todo → scheduled → ready → running → blocked → review → done → archived
  ↓        ↓                   ↓         ↑          ↓
  │        └─ 等待依赖完成 ──────────────┘          │
  └─ 粗糙想法 → decompose/specify → promote         │
            ┌───────────────────────────────────────┘
            │  重试：crashed → reclaim → ready → running
            │  断路：spawn_failed × N → gave_up → blocked
            │  阻塞：worker calls kanban_block()
            │  超时：max_runtime exceeded → timed_out → ready
```

edict 7 状态硬编码 `VALID_TRANSITIONS`（dispatch.py）：

```
triage   → {todo, archived}
todo     → {ready, archived}
ready    → {running, blocked, done, archived}
running  → {done, blocked, archived}
blocked  → {ready, archived}
done     → {archived}
archived → (终点)
```

### 4.9 任务标准态（监国方案语义）

```
intake → drafted → reviewing → approved → dispatched → running → reported → verified → archived → delivered
```

异常态：blocked / failed / timeout / over_budget / permission_denied / needs_user_decision / rejected / escalated。

---

## 五、治理流程

### 5.1 九步标准流程

```
监国接旨 → 中书拟制 → 门下封驳 → 尚书派工 → 六部办事 → 御史监察 → 门下复核 → 史馆归档 → 监国复命
```

### 5.2 三路分流

| 路 | 适用 | 流程 |
|----|------|------|
| A 路 | 部门内部待办 | 尚书省 dispatcher → 门下审核 → 工单流转 |
| B 路 | 外部专家（Claude Code/Codex） | 吏部 registry 验证资质 → 工单注入 → 御史独立审计 |
| C 路 | 固定流程（门下预审批）| 跳过中书门下直接执行 / cron |

### 5.3 vNext 门下两职分离

| 阶段 | 审什么 | 禁审 |
|------|--------|------|
| 封驳 | 目标 / 范围 / 预算 / 权限 / 风险 / 重复 / 验收标准 | 代码细节 |
| 复核 | 对照验收标准定可否交付 | 补源改稿 |

### 5.4 plan-preview 触发条件

| 条件 | 必须 plan-preview | 轻量规格 | 不得碰细节 |
|------|:---:|:---:|:---:|
| 多执行步骤 / 跨领域 / 视觉交付 / 多轮验收 | ✅ | — | — |
| 视觉交付（HTML / 图片 / 视频） | ✅ | — | — |
| 跨领域（>1 skill 域） | ✅ | — | — |
| 单点查证 / 事实确认 / 配置读取 | — | ✅ | — |
| 已知固定链路（如早新闻） | — | ✅ | — |
| 代码 / 命令 / 文件路径细节 | — | — | ✅ |

### 5.5 降级升级规则

| 场景 | 动作 |
|------|------|
| 低风险任务误入繁务流程 | 降级为简务直批 |
| 高风险任务缺 plan-preview | 升级要求补 plan-preview |
| 返修 >2 次 | 升级奏报父皇 |
| 恢复 >2 次 | 升级，太子手动裁决 |
| 预算 / 超时超出常规 | 升级，需父皇确认 |

### 5.6 尚书省恢复策略矩阵

| 异常类型 | 检测条件 | 自动恢复动作 | 升级条件 |
|---------|---------|------------|---------|
| stalled | running + 无 heartbeat > 5min | reclaim + reassign | recovery_count ≥ 2 → Decision Card |
| blocked | status = 'blocked' | 读取 reason 判断自动解阻 | 需人工 → Decision Card |
| timed_out | outcome = 'timed_out' | reclaim + 拆分为小子任务 | 连续 2 次 → 升级太子 |
| crashed | outcome = 'crashed' | reclaim + 检查 workspace | 连续 2 次 → 升级太子 |

### 5.7 能力-部门映射表（尚书省派工依据）

| 关键词 / 任务类型 | 首选 assignee | 备选 | 部门 |
|----------------|--------------|------|------|
| 代码 / 开发 / 重构 / bugfix | engineer | jiangzuojian | 工部 / 将作监 |
| 测试 / 验证 / 审计 / 合规 | tester | auditor | 刑部 / 御史台 |
| 安全 / 渗透 / 审查 | security | auditor | 刑部 / 御史台 |
| 文档 / 写作 / 协议 / 对外 | protocol | hanlinyuan | 礼部 / 翰林院 |
| 预算 / 成本 / 资源 | budget | — | 户部 |
| 应急 / 故障 / 巡检 | emergency | — | 兵部 |
| 归档 / 知识库 / ADR | archivist | — | 史馆 |
| Agent 管理 / 注册 | registry | — | 吏部 |
| 研究 / 分析 / 规划 | planner | hanlinyuan | 中书省（需太子特批） |
| 审核 / 封驳 | reviewer | — | 门下省（需太子特批） |

### 5.8 6 条硬红线

1. 执行者不能自审
2. 审核者必须有否决权
3. 任务必须带 `timeout / budget / acceptance_criteria`
4. 外部专家必须独立审计（声称完成 ≠ 真完成）
5. 禁止共享同一上下文，传递信息只走结构化任务书
6. ADR 裁决权独占门下

### 5.9 最小宪法十条

1. 先查制度再办事
2. Hermes 事务先查 hermes-agent skill
3. 说做即做
4. 能查不问
5. 禁凭印象断案
6. 必验后奏
7. 外部动作须请旨
8. 少扰民
9. 记忆只存长期事实
10. 各司印信独立

### 5.10 角色身份隔离（防附体）

`task_handler.py` 的 `identity_prefix` 必须 profile-aware（ADR-0004）：

```python
profile = os.environ.get('HERMES_PROFILE', '')
if profile == 'regent':
    identity_prefix = '【系统提示】... 你的身份：监国太子(regent)...'
else:
    identity_prefix = (
        '【系统提示】你正在通过 A2A 协议接收任务。'
        '你的身份：小黄（主频道助手），用户的个人助理。'
        '你独立于三省六部体系之外...'
    )
```

> task_handler.py 的身份前缀之前硬编码为「小黄」，导致 regent 的 A2A 通道也被注入小黄身份。

---

## 六、测试体系

### 6.1 五层金字塔

```
L4  非功能   性能 · 故障 · 安全
L3  E2E治理  多场景全链路（中书→门下→尚书→六部→史馆）
L2  集成     A2A+Kanban · 讨论 · 事件桥 · 跨部门调用
L1  组件     每模块独立（A2A Server / Kanban / Discuss / Event Bridge / Skill Resolver / DCI）
L0  基础健康  进程·端口·注册表·Gateway·Supermemory
```

在传统 unit/integration/e2e 之外补 L0（每日可跑环境信号）与 L4（性能 / 故障 / 安全）。

### 6.2 L3 E2E 5 场景结果（2026-05-31）

| 场景 | 通过 | 耗时 | commit |
|------|:----:|------|--------|
| S1 健康扫描 | 10/10 | ~1200s | eb18a9c |
| S2 代码审查 | 10/10 | 998s | 469c182 / 1c94c48 / 12d2368 |
| S3 早新闻 | 7/7 | 396s | 48344b8 |
| S4 制度修改 | 8/8 | 632s | 0ebb7f5 |
| S5 双次封驳 | 9/9 | 903s | 07db373 |
| **合计** | **44/44** | **~4200s (~70min)** | — |

L4 非功能 5/5 全绿 < 1min（commit `cd6362c`），总计 **49 项全通**。

### 6.3 L0 验收 10 项

```
1 进程+解释器        15+ server.py 全 venv 3.11 ✅
2 注册表完整性       15 profiles，端口无重复 ✅
3 健康+Agent Card    16/16 health 200，card 含 name ⚠️
4 Token 共享         ~/.hermes/.a2a-token 存在，跨 profile 认证通过 ✅
5 端口稳定性         3× bootout/bootstrap 端口不变 ⏭️
6 A2A 任务           POST → poll → completed ❌→#1
7 Doctor 全面检查    8/8 pass ⚠️ 8/10
8 僵尸检测           无非 venv 进程 ✅
9 Gateway 健康       3 core gateway 全 running ✅
10 Supermemory 连通  supermemory_search 正常返回 ✅
```

### 6.4 S1 健康扫描链路

```
planner → reviewer → shangshu → budget ∥ gongbu → protocol → tester → reviewer → archivist
```

10 项验收：六部全触发 / 门下审查 / 尚书协同 / 工部基扫 / 户部预审 / 礼部汇总 / 测试稽核 / 史馆归档 / 总耗时 ≤ 25min / Kanban 完整。

### 6.5 S5 双次封驳韧性链路

```
planner → reviewer ❌ → planner → reviewer ❌ → planner → reviewer ✅ → shangshu → archivist
```

9 项验收：封驳 ≥ 2 次 / 返修链 = 2 轮 / 终审通过 / 尚书省协调 / 史馆归档 / 总耗时 ≤ 25min / 无僵尸进程 / Kanban 完整。

### 6.6 P0 TDD 单元/集成总账

| 套件 | 用例数 | 状态 |
|------|:----:|:----:|
| P0-1 Unit kanban init | 4/4 | ✅ |
| P0-1 Integration card lifecycle + daemon + dispatch | 6/6 | ✅ |
| P0-1 E2E regent 全链路 + doctor check 9/10 | 2/2 | ✅ |
| P0-2 Unit skill resolver 4 层 + M2CL 跨部门 | 8/8 | ✅ |
| P0-2 Integration dispatch skills + worker env | 6/6 | ✅ |
| P0-3 Unit DCI 旁路表 14 kind CHECK | 6/6 | ✅ |
| P0-3 Integration comment_kind API + orchestrator 路由 | 7/7 | ✅ |
| **合计** | **39/39** | ✅ 12.86s + E2E 2 pass |

### 6.7 L4 非功能

- **安全**：Bearer Auth 强制（无 / 错 token → 401）2/2，跨 profile 隔离（任务不可跨读）1/1
- **韧性**：宕机恢复（kill archivist → launchd 自愈 ≤ 0.0s）1/1，任务持久化（重启后数据不丢）1/1
- **合计 5/5 全绿 < 1min**

### 6.8 TDD 红绿循环

> TDD 方法：每个场景先写测试（红灯）→ CC agent team 跑通（修复 → 绿灯）→ commit + push。

每发现缺陷即回写「八.问题登记」，使后验改进可追溯（11 条问题登记证实）。

### 6.9 测试文件清单

```
tests/unit/
├── test_kanban_init.py            (4)
├── test_per_task_skill.py         (8 含 4×M2CL parametrize)
└── test_comment_kind.py           (6)
tests/integration/
├── test_card_lifecycle.py         (6)
├── test_skill_dispatch.py         (6)
└── test_thread_protocol.py        (7)
tests/e2e/
├── test_l3_s1_health_scan.py      # S1 健康扫描 10/10
├── test_l3_s2_code_review.py      # S2 代码审查 10/10
├── test_l3_s3_morning_news.py     # S3 早新闻 7/7
├── test_l3_s4_governance_change.py # S4 制度修改 8/8
├── test_l3_s5_double_rebuke.py    # S5 双次封驳 9/9
├── test_l4_nonfunctional.py       # L4 非功能 5/5
├── test_p0_matrix_16_profile.py   (222 行)
├── test_p0_debate_e2e.py          (429 行)
├── test_first_card_e2e.sh
└── test_doctor_checks.sh
tests/conftest.py                  (~190 行 7 fixture + JZ_SKILLS_ROOT 加固)
```

> profiles/ 15GB，conftest 必须用 symlink 而非 copytree（110× 加速）；同时引入 `JZ_SKILLS_ROOT` env 防 HOME 劫持破坏 `Path.home()`。

---

## 七、运维手册

### 7.1 doctor.sh 8 项自动检查

| 检查 | 内容 |
|------|------|
| check_python_interpreter | server.py 进程必须用 venv python3.11 |
| check_fallback_chain_self_loop | fallback 与 main model 不可同 provider+model |
| check_port_uniqueness | `lsof -iTCP -sTCP:LISTEN` 每端口仅 1 PID |
| check_home_hack_leak | `launchctl print` 不应显示 sandbox HOME 路径 |
| check_core_deploy_drift | `diff -rq core/ deploy/` |
| check_provider_key_liveness | 每 provider 1-token 测试请求 |
| check_send_message_tool_in_a2a | A2A 上下文需能拿到 telegram credentials |
| check_identity_prefix_profile_aware | `grep HERMES_PROFILE` in `task_handler.py` |

### 7.2 P0 问题速查表

| 编号 | 问题 | 根因 |
|------|------|------|
| P0-01 | MiniMax-M2.7 HTTP 404 | fallback 自指 + 错误 base_url |
| P0-02 | DeepSeek API key 失效 | HTTP 401 |
| P0-03 | regent 被附身「小黄」 | task_handler.py 身份前缀硬编码 |
| P0-04 | 源 / 部署双向不同步 | 缺乏 drift 检查 |
| P0-05 | 15+ A2A 僵尸 / 陈旧 gateway 进程堆积 | System Python 3.9 不能处理中文 Unicode |
| P0-06 | regent 端口 8643 冲突 | PORT_RANGE 早期 50 太小 |
| P0-07 | launchctl bootstrap `Load failed: 5: Input/output error` | HOME hack 泄漏 |
| P0-08 | API Server 拒绝启动 | API_SERVER_KEY 缺失 |

### 7.3 Runbook §3.2 — A2A 僵尸堆积

```bash
# 诊断
ps aux | grep "hermes-a2a/server.py" | grep -v grep
ps aux | grep server.py | awk '{print $11}' | sort | uniq -c
plutil -p ~/Library/LaunchAgents/com.hermes.a2a.<profile>.plist | grep -i python

# 修复
pkill -f "hermes-a2a/server.py"
HOME=/Users/user launchctl bootout gui/501/com.hermes.a2a.<profile>
HOME=/Users/user launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.hermes.a2a.<profile>.plist

# 验证
ps aux | grep server.py | grep -v grep | wc -l    # 期望 = 1 per profile
curl http://127.0.0.1:<a2a_port>/health
curl http://127.0.0.1:<a2a_port>/a2a/.well-known/agent-card.json | jq .name
```

### 7.4 HOME hack 陷阱

Hermes session 把 HOME 改成 profile 沙箱（`~/.hermes/profiles/<profile>/home/`），导致 launchctl / plutil 找不到真 `~/Library/LaunchAgents/`；**所有 launchctl 命令必须前置 `HOME=/Users/<real-user>`**。

| 视角 | 路径 |
|------|------|
| Daemon 视角（真实 HOME） | `~/.hermes/supermemory.json` |
| Session 视角（profile home） | `$HERMES_HOME/supermemory.json` |

诊断跨进程问题时，必须同时在两个路径下检查。

### 7.5 Supermemory 双池故障修复全记录（2026-05-30）

```
05-28 ~ 05-30 15:10  → 旧代码 bypass  → hermes-cabinet（218 篇）
15:10–15:13           → Gateway 重启   → 触发点
15:13–15:28           → 新代码 sanitize → hermes_cabinet（5 篇）
```

| # | 项目 | 操作 | 状态 |
|---|------|------|------|
| P0 | 改正则 | git pull 144 commits，`_sanitize_tag("hermes-cabinet") → "hermes-cabinet"` | ✅ |
| P0 | 创建 supermemory.json | 写入 `~/.hermes/supermemory.json`（真实 HOME），regent → hermes-cabinet | ✅ |
| P0 | 重启 Gateway | 3 个 core gateway（default/regent/cron-worker）kickstart | ✅ |
| P0 | 重启 daemon | Event Bridge daemon 重启（PID 65890），dispatch 零 timeout | ✅ |
| P1 | 统一 schema | daemon 与 session 读取同一 supermemory.json | ✅ |
| P1 | Re-tag 旧文档 | 5 篇 hermes_cabinet → hermes-cabinet | ⚠️ 待 Dashboard 手动操作 |

### 7.6 Gateway 监管命令

```bash
hermes -p default gateway install   # 重装 plist
hermes -p default gateway start
hermes -p default gateway restart
hermes --profile <target> config set <key> <value>  # 跨 profile 必须显式 --profile
```

`scripts/gateway-wrapper.sh`（176 行）Preflight 六项检查：venv python 可执行性、HERMES_HOME 存在性、config.yaml 可读性、kanban.db 完整性、.env 存在性、过期 PID 文件清理；killpg SIGTERM 拦截 → pkill -P 子进程树 → 25s 优雅等待 → SIGKILL 兜底。

### 7.7 Kanban / Registry 命令速查

```bash
# Kanban
hermes kanban init
hermes kanban create "title" --assignee <profile> --skill <name>
hermes kanban show <id>
hermes kanban complete <id> --summary "..." --metadata '{...}'

# Registry CLI
python ~/.hermes/plugins/hermes-a2a/registry.py             # JSON dump
python ~/.hermes/plugins/hermes-a2a/registry.py port <p>    # bare port number
python ~/.hermes/plugins/hermes-a2a/registry.py cleanup     # prune stale entries

# 健康检查
bash ~/code/hermes-a2a/core/scripts/hermes-a2a-doctor.sh
```

### 7.8 性能预算

| 指标 | 阈值 |
|------|------|
| cache hit `memory.search` | < 10ms |
| cache miss | < 200ms |
| `memory.add` | < 300ms（含缓存失效） |
| 启动构建 | < 250ms（含 2 个预热 query） |
| 每轮动态段构建 | < 100ms（命中）/ < 250ms（miss） |
| 缓存命中率 | Phase 2 末 ≥ 60%、Phase 3 末 ≥ 75% |
| ROLEPLAY R1 冷启动 | ~141s（67-216s，冷启动惩罚 4.9×） |
| ROLEPLAY R2 缓存 | ~29s（13-47s） |
| pre_tool_call p99 延迟 | bridge 启用前后差 < 5ms |
| daemon SIGTERM 优雅等待 | 25s 后 SIGKILL |

### 7.9 严重度分级

| 级别 | 定义 |
|------|------|
| P0 | 阻断性（A2A 通道不可用 / 模型不可用 / 数据-身份污染） |
| P1 | 严重影响（功能降级 / 手动 workaround / 影响审计） |
| P2 | 优化改进（噪音 / 设计议题 / 边角） |

---

## 八、关键决策（ADR 摘要 001-006）

### ADR-001 采用 A2A Protocol 作为跨 profile 通信主干

- **替代方案**：Kanban 轮询（无同步、无能力发现）、原始 HTTP API（无标准化）、agentmemory 共享层、自建 EmpireThread P4
- **理由**：A2A 是 Google → Linux Foundation 开放标准（23.4K⭐），Agent Card + Task + Stream 三件套覆盖能力发现 / 任务委派 / 流式进度全部需求；HTTP/JSON 传输层与内阁 API 桥接完全兼容

### ADR-002 共享代码 + per-profile symlink 部署

- **替代方案**：纯全局插件（无 profile 隔离）、每 profile 独立拷贝（更新困难）
- **理由**：代码一份存 `~/.hermes/plugins/hermes-a2a/`，15 profile 共享；端口通过 `hash(profile_name) % 300 + 8650` 自动分配零碰撞

### ADR-003 HTTP/JSON 先行，JSON-RPC 2.0 后补

- **替代方案**：JSON-RPC 一步到位、纯 REST
- **理由**：A2A 规范同时支持两种；当前内阁 API 桥接已验证 HTTP+JSON 可行，渐进推进

### ADR-004 identity_prefix 改 profile-aware（防附体）

- **替代方案**：硬编码「小黄」身份前缀、完全不注入身份
- **理由**：硬编码导致角色串味；profile-aware 通过 `HERMES_PROFILE` 环境变量分支；同时 discuss.py ROLEPLAY/SYNTHESIZE prompt 模板改为「小黄，独立于三省六部体系之外」

### ADR-005 EventBridge 长期记忆 sink 由 Hindsight 替换为 Supermemory

- **替代方案**：保留 Hindsight L1 + Supermemory L2 双层、Hindsight 单层、新建 vector DB
- **理由**：Hindsight 从未启用过（缺 API key，daemon 启动即跳过 sink 注册）；三省六部全线已用 Supermemory，container_tag 映射就绪；EventBridge 收敛为 Obsidian + Supermemory 双 sink
- **结果**：52/52 测试 GREEN（删 10 Hindsight，加 9 Supermemory）

### ADR-006 Core Gateway 统一由 launchd + gateway-wrapper.sh 监管

- **替代方案**：手动 `--replace` 模式（无自动重启）、systemd（macOS 不原生）、supervisord（多一层依赖）
- **理由**：与 A2A 16 profile 的 launchd 监管模式对齐；统一 preflight 减少无效重试；killpg wrapper 确保干净退出
- **细节**：cron-worker API 端口由 8460 改为 8461（与 A2A 端口分离）

### 配套决策（监国方案 / vNext / 尚书省升级）

| 决策 | 摘要 |
|------|------|
| 监国分权 | 采用「监国三省六部制」而非平铺专家 |
| 结构化任务书 | YAML schema 取代共享上下文 |
| 星型先行 | 初期星型拓扑，稳定后开有限 A2A 横向 |
| 审计独立 | 御史台只查不改 / 史馆只记不造，二者必须在执行链之外 |
| fan-in 创建时绑 parents | 禁止事后 link 兜底（避免 ready/claim 竞态） |
| 门下两职分离 | 封驳 vs 复核两阶段 |
| 返修阈值统一 | 最多 2 次，第 3 次升级太子 / 父皇 |
| handoff_schema v2 严格超集 | 仅追加 3 字段，向后兼容 |
| 尚书省升级 dispatcher | 不新建 profile，AI 智能层坐于 gateway dispatcher 之上 |
| 能力映射表硬编码先行 | Phase 1 YAML/JSON，Phase 2 评估动态化 |
| cron no_agent 跟踪 | 正常零 LLM 成本，异常按需触发 agent |
| Sanitize 显式 allowlist | `[^a-zA-Z0-9_-]` 替代 deny-by-default |
| Supermemory Sink best-effort | 故意不实现 L0-L3 四层降级 |
| memory 工具方案 C | 保留入口、底层切到 wrapper |
| 缓存层选 LMDB | 嵌入式 KV，跨进程，纳秒级读，零运维 |

---

## 九、路线图

### 9.1 已完成里程碑

| 日期 | 里程碑 |
|------|--------|
| 2026-05-20 | v2 底座修复 |
| 2026-05-25 | 三大支柱骨架 |
| 2026-05-26 | 12-Factor 三件套：EmpireThread + context_tags + human_input_tool |
| 2026-05-28 | v0.2 A2A 功能（A2A 16/16 healthy · Doctor 8/8 pass · 零僵尸） |
| 2026-05-29 | A2A v0.15.x 关键审计决策（PR #11025 不采纳） |
| 2026-05-30 | EmpireThread v2 事件桥（~700 行 Python，52/52 测试全绿） |
| 2026-05-30 | Supermemory 双池故障修复（正则 allowlist 化） |
| 2026-05-31 | L3 E2E 5 场景 44/44 全绿 + L4 5/5 全绿 |

### 9.2 5 大方向（按优先级）

1. **EmpireThread 事件桥打通多记忆系统**（v2 已完成）
2. **上线专业化 profiles**（A2A 服务 16/16 全绿）
3. **v0.3 A2A 协议增强**（GET 列表 + SSE 流式已实现，fan-out 编排待规划）
4. **审计全闭环**（已完成，低分触发 Telegram 告警 + Kanban 复审卡）
5. **治理流程补正**

### 9.3 尚书省实施分三阶段

| Phase | 内容 | 工期 | 验收 |
|-------|------|------|------|
| 1 | 智能派发 | 2-3 天 | PRD 拆卡准确率 ≥ 80% |
| 2 | 主动协调 | 1-2 周 | 自动恢复率 ≥ 70% |
| 3 | 汇总呈报 | 1 周 | 3 组 fan-in 报告通过门下审核 |

### 9.4 Supermemory 记忆架构 Phase 时间线

| 周次 | 内容 |
|------|------|
| Week 1 | Phase 1（基础 + 工具改造 + Hindsight 退役，5-7d） |
| Week 2 | Phase 2（全员铺开 + 注入器 + 缓存，7-10d） |
| Week 3 | Phase 2.5（跨池查询通道，并行不阻塞） |
| Week 3-4 | Phase 3（监控 + ADR 链接 + 文档，1-2 周） |

### 9.5 vNext 验收量化标准

| AC | 标准 |
|----|------|
| AC1 | 路由准确率 ≥ 90% |
| AC2 | handoff_schema v2 投用 |
| AC3 | 封驳不审代码 |
| AC4 | 返修 ≤ 2 |
| AC5 | 恢复 ≤ 2 |
| AC6 | SOUL.md 由 206 行缩至 ≤ 120 行 |
| AC7 | constitution skill v1.0.0 → v2.0.0 |

### 9.6 待办与残留

- 5 篇 `hermes_cabinet` 旧文档待 Dashboard 手动 re-tag（P1）
- 尚书省方案 C1（coordinator-poll → kanban-watcher 全文替换，7 处）
- 尚书省方案 C2-C5（CAS 锁源码路径、heartbeat 字段存在性、fan-in 触发机制、cron schema 依赖）
- 11 profile 当前显示离线（archivist / auditor / dispatcher / gongbu / hanlinyuan / jiangzuojian / planner / protocol / registry / reviewer / tester），为 2026-05-28 快照采样而非稳态 SLA [待确认]

---

## 十、关联资源

### 设计文档

- [[三省六部×A2A架构方案_20260529]]
- [[三省六部Agent体系执行宪章与落地方案_20260519]]
- [[监国三省六部制Agent架构方案]]
- [[CONTEXT]]
- [[Hermes_路线图_v1.0]]
- [[EmpireThread_事件桥_综合设计文档_v1.0]]
- [[Supermemory三省六部记忆架构设计_v2.0]]
- [[hermes-a2a_方法论文档]]

### 制度与决策记录

- [[三省制度vNext方案_待实施制度包_经三省审订+御史稽核通过_20260524]]
- [[尚书省升级方案_执行总枢AIAgent_待实施制度包_经三省审订+御史稽核通过_20260525]]
- [[Agent名册(三省六部制)]]

### 测试与质量

- [[三省六部全面测试方案_20260530]]
- [[E2E测试结果汇总_20260531]]
- [[三省六部A2A_TDD实施总结_20260529]]

### 追踪与排障

- [[hermes-a2a_项目追踪]]
- [[hermes-a2a_部署配置说明]]
- [[Hermes A2A 问题收集与排障手册]]
- [[hermes-a2a_Supermemory双池审计_20260530]]
- [[三省六部能力图谱]]

### 关联背景

- A2A Spec (Google → Linux Foundation, 23.4K⭐)
- 三省六部宪章 skill (constitution v2.0.0)
- 内阁群 Telegram chat（人类交互入口）
- jz-skills 仓库（GitHub: `<org>/jz-skills`，53 个 SKILL.md，MIT 许可）

---

> **版本说明**：v2.0 整合 5 个 tier 的 reader 提取要点，覆盖架构 / 部署 / 通信 / 治理 / 测试 / 运维 / ADR / 路线图 八大维度；下一版本（v2.1）需补全：(1) 测试体系 L1/L2 完整覆盖矩阵 (2) 关联资源章节资源 ID 化 (3) 兵部 / 刑部职责在监国方案 v1.0 vs 执行宪章 v1 之间的版本差异统一说明。
