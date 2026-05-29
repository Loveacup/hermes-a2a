# EmpireThread 事件桥 — v2 缩窄版

> **状态**：设计修订 ✅ | 代码与测试已落地 ✅ | 生产 emit/launchd 待打通 ⏳
> **日期**：2026-05-29（v2）· 2026-05-30（ADR-005 Hindsight → Supermemory 修订）
> **修订背景**：
> 1. W3 调研 hermes v0.15.x 跨 profile 通知机制（`kanban.notification_sources` / `kanban_notify_subs.notifier_profile`）后，对 v1.0 的 4-Sink 架构作"职责切割"
> 2. 2026-05-30：Hindsight 从未启用（无 API key、daemon 每次启动跳过），且三省六部全线已用 Supermemory（API key + container_tag 映射已就位），按 ADR-005（决策依据 ARCH-TEST-001）将第二个 sink 由 **Hindsight** 替换为 **Supermemory**，最终收敛为 `Obsidian + Supermemory` 单层架构
> **基线**：[[EmpireThread_事件桥_综合设计文档_v1.0]]
> **关联**：[[Hermes_路线图_v1.0]] · [[三省六部×A2A架构方案_20260529]] §11.1 · [[methodology#ADR-005]]

---

## 一、为什么要修订

### 1.1 v1.0 的 4-Sink 与 v0.15.x 官方能力的重叠

| Sink | v1.0 用途 | v0.15.x 是否有官方替代 | 修订动作 |
|------|----------|----------------------|----------|
| **Sink 1: MEMORY.md** | 跨 profile boot-critical 事实推送 | ✅ `kanban_notify_subs` 跨 profile 投递（PR #28395 + 后续修复） | **去除** |
| **Sink 2: Obsidian** | 知识库文档（ADR / 事件日志）写入 | ❌ 官方无 | **保留** |
| **Sink 3: Hindsight** | 长期语义记忆写入 | ❌ 官方无 | **保留**（v2）→ **替换为 Supermemory**（ADR-005, 2026-05-30） |
| **Sink 4: Session DB** | 跨 profile 会话历史事件驱动 | ✅ 同 Sink 1（kanban 自身的 events + runs 表已是事件源） | **去除** |

**结论**：EmpireThread 必须存在的核心理由 = Obsidian + 长期记忆写入。其余两个 Sink 在 v0.15.x 已被官方原生能力覆盖。

> **2026-05-30 修订**：第三栏「长期语义记忆写入」的 backend 由 Hindsight 替换为 Supermemory，详见 §2.1a + §3.5b + ADR-005。原因：Hindsight 从未启用（无 API key），三省六部全线已 Supermemory 化，双层架构无收益。

### 1.2 v0.15.x notification_sources 的真实机制（W3 调研产出）

文档承诺与代码现实存在 **显著 gap**，影响后续设计：

**官方文档（`skills/devops/kanban-worker/SKILL.md`）：**

```
notification_sources: ['*']             # 接受所有 profile 订阅
notification_sources: ['default','x']   # 限定来源 profile
omitted                                 # profile 隔离（默认）
```

**v0.15.1 代码现实**（`git grep notification_sources -- '*.py'` 结果：**0 处引用**）：

- `kanban_notify_subs` 表新增 `notifier_profile` 列（PR #28395 主体改动）
- 创建订阅时记录"当前 gateway profile"为 owner
- watcher 严格按 `owner_profile == current_gateway_profile` 过滤（`gateway/run.py:4974-4980`）
- **没有任何 Python 代码读取 `~/.hermes/config.yaml` 的 `kanban.notification_sources` 配置项**

**真实可用的跨 profile 通知模式 = "接收方自建订阅"**：

```
事件源 profile A 完成任务（写 task_events 行到共享 kanban DB）
                       │
                       ▼
接收方 profile R 的 gateway watcher 看到 owner=R 的订阅
                       │
                       ▼
R 自己的 adapter（telegram/discord/...）推送
```

→ R 想接收 A 的通知，**R 必须在自己 profile 上预先创建订阅** `hermes -p R kanban notify-subscribe <task>`。共享 kanban DB 是事件总线，每个 gateway 只负责投递 own 订阅。

→ **跨 profile fan-out 不再是 EmpireThread 的职责**，已是 kanban 原生能力（订阅方主动拉取模型）。

### 1.3 修订原则

1. **不与官方能力重复造轮子**：能用 kanban_notify_subs 达成的就不做新 Sink
2. **保留独有价值**：Obsidian 知识库写入 / 长期语义记忆写入（Supermemory）— 官方无对应物
3. **G1-G5 加固项按风险面重估**：Sink 减半 → 部分加固降级
4. **不重复造已建好的轮子（ADR-005 增补）**：长期记忆 backend 已在三省六部全线 Supermemory 化，废除 Hindsight + Supermemory 双层，单 sink 收敛
5. **代码量目标**：v1.0 ~550 行 → v2 ~300 行（Supermemory sink 进一步把 Hindsight 的 196 行简化到 ~80 行）

---

## 二、v2 架构

### 2.1 数据流

```
事件源（A2A handler · kanban_event · pre_tool_call hook）
    │  hook 同步写入
    ▼
EmpireThread JSONL（已实现 ✅，~/.hermes/profiles/<p>/empire-thread.jsonl）
    │  fsevents + 1s 兜底轮询
    ▼
EventBridge daemon（launchd KeepAlive=true，wrap zsh 源 ~/.hermes/.env）
    ├── Sink: Obsidian            ← ADR / 事件日志 / 决策记录 md 文件，per-sink pending 队列
    └── Sink: Supermemory         ← POST https://api.supermemory.ai/v3/documents（best-effort 直发）

跨 profile 任务完成通知 → 直接走 kanban_notify_subs（不经 EmpireThread）
跨 profile 共享事实        → 直接 commit 到共享 kanban + 接收方订阅
```

### 2.1a 长期记忆 sink 替换记录（ADR-005, 2026-05-30）

v2 设计中第二个 sink 原为 `sinks/hindsight.py`（REST PUT /memories + 4 层降级），实施过程中发现：

| 维度 | Hindsight（原方案） | Supermemory（替换） |
|------|--------------------|--------------------|
| API key 状态 | `HINDSIGHT_API_KEY` 从未配置 | `SUPERMEMORY_API_KEY` 已在 `~/.hermes/.env` |
| daemon 启动行为 | 永远跳过实例化 | 实际注册并 dispatch |
| 集成现状 | 仅 event_bridge 一处 | 三省六部全线已用（container_tag 映射齐备） |
| HTTP 端点 | `PUT /memories` | `POST https://api.supermemory.ai/v3/documents` |
| 鉴权 | Bearer `HINDSIGHT_API_KEY` | Bearer `SUPERMEMORY_API_KEY` |
| Payload | snake_case | camelCase（`containerTags`/`customId`/`metadata`） |
| 实现复杂度 | 196 行（含 4 层降级 + pending + DLQ） | ~80 行（urllib best-effort 直发） |
| 容错策略 | L0(2s) → L1 重试 → L2 DLQ → L3 熔断 60s | 失败吞掉记 warning，不入队、不重试、不阻塞 cursor |

替换详情见 §3.5b、§五实施路线、ADR-005。

### 2.2 删除/保留对照

| v1.0 组件 | v2 状态 | ADR-005 后状态 | 备注 |
|----------|---------|-----------------|------|
| `event_bridge/core.py`（dispatch + 倒排索引） | ✅ 保留 | ✅ | 倒排索引在 2-Sink 下可简化为直接遍历 |
| `event_bridge/cursor.py`（per-Sink cursor） | ✅ 保留 | ✅ | 增量消费仍必要 |
| `event_bridge/daemon.py`（launchd sidecar） | ✅ 保留 | ✅（条件改 `SUPERMEMORY_API_KEY`） | G1 异步约束未变 |
| `sinks/memory.py`（MEMORY.md flock + 原子 rename） | ❌ **删除** | ❌ | 让位 kanban_notify_subs |
| `sinks/obsidian.py` | ✅ 保留 | ✅ | 这是缩窄版核心 |
| `sinks/hindsight.py` | ✅ 保留 | ❌ **删除（-196 行）** | 替换为 `sinks/supermemory.py` |
| `sinks/supermemory.py`（新） | — | ✅ **新建（~80 行）** | ADR-005 唯一长期记忆 sink |
| `sinks/session.py`（Session DB 重构） | ❌ **删除** | ❌ | kanban events/runs 表已是事件源 |
| `pending/`（per-Sink pending.jsonl） | ✅ 保留 | ⚠️ 仅 Obsidian sink 走队列；Supermemory 不入队 | ADR-005 简化 |
| `dlq/`（死信） | ✅ 保留 | ⚠️ 仅 Obsidian sink；Supermemory 无 DLQ | ADR-005 简化 |

---

## 三、G1–G5 加固项重评

### 3.1 G1 — 异步 out-of-band Dispatch · **仍需**

**理由不变**：长期记忆 sink（v2 原 Hindsight；ADR-005 后 Supermemory）网络调用 P99 数百毫秒级，绝不能阻塞 pre_tool_call hook。launchd sidecar + fsevents + 1s 轮询的 v1.0 方案直接沿用。

**验收**：pre_tool_call p99 延迟启用 bridge 前后差 < 5 ms

**实施清单**：`~/.hermes/bin/event_bridge_daemon.py` · `~/Library/LaunchAgents/com.hermes.eventbridge.plist`

### 3.2 G2 — Cursor 增量消费 · **保留 + 倒排索引简化**

**倒排索引在 2-Sink 下不必要**：14 事件类型 × 2 Sink 矩阵足够稀疏，O(14) 直接遍历足以满足 1M 事件 < 5 ms 门禁。**保留 cursor 增量消费**（行号 + byte_offset + inode），删除倒排索引代码（约 -30 行）。

**Cursor 推进逻辑沿用 v1.0**：

```python
def consume_for(self, sink: Sink) -> None:
    cur = CursorStore.load(sink.name)
    st = os.stat(JSONL_PATH)
    if cur.inode != st.st_ino:
        cur = Cursor("", 0, 0, st.st_ino, 0.0)  # rotate → cold start
    with open(JSONL_PATH, "rb") as f:
        f.seek(cur.byte_offset)
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # 半行：留到下次
            evt = json.loads(raw)
            if sink.accept(evt):
                enqueue(sink, evt)
            cur.lineno += 1
            cur.byte_offset = f.tell()
        CursorStore.save_atomic(sink.name, cur)
```

**门禁不变**：1M 事件 · 100 条增量 tick < 5 ms

### 3.3 G3 — `_source` 白名单 + 重入计数 · **降级保留**

**风险面变化**：

| Sink | 自触发风险 | 评估 |
|------|----------|------|
| ~~MEMORY.md~~ | ~~HIGH（pre_tool_call hook 监听文件写入 → 死循环可能）~~ | 已移除 |
| Obsidian | LOW（写 vault md 不进 EmpireThread 事件源） | 防御性保留 |
| Supermemory（ADR-005 替换 Hindsight） | NEAR-ZERO（POST API 不产生 hermes 事件） | 不必单独防御 |

**v2 方案**：

- ✅ **保留**：`_source = "sink_writeback"` 白名单（声明式，~10 行，防 Obsidian/Supermemory 未来扩展产生回路）
- ❌ **删除**：`threading.local` 重入计数（无 MEMORY.md 后无真实跨进程回路可拦）
- ❌ **删除**：触发熔断的 60 s OPEN（让位 G5 的 DLQ 单独管 Obsidian 重试；Supermemory 已是 best-effort 无重试，详见 §3.5b）

**代码量**：v1.0 ~50 行 → v2 ~10 行

### 3.4 G4 — MEMORY.md `flock` + 原子 rename · **完全删除**

**整个 MEMORY.md Sink 移除后，G4 失去意义**。v1.0 设计中的 `fcntl.flock` + `tmp + os.replace` + SHA256 乐观锁三件套全部删除。

**回归路径**：MEMORY.md 由 hermes-cli 原生的 memory tool / boot context 机制单独维护，不再由 EmpireThread 异步写入。

**代码量**：v1.0 ~80 行 → v2 0 行

### 3.5 G5 — Bridge 队列强制落盘 · **保留（仅 Obsidian sink）**

**v2 原设计**：剩余两个 Sink 中，Hindsight 是网络 IO，pending.jsonl + DLQ + 三段重试是必须的；Obsidian 走统一队列。

**ADR-005 后实际方案**：
- **Obsidian sink**：仍走 pending 队列（本地 IO 也可统一恢复路径，无额外成本）
- **Supermemory sink**：**不走 pending 队列**，简化为 best-effort 直发 —— 详见 §3.5b

**沿用 v1.0 不变量（适用于 Obsidian sink）**：

- 入队 = `O_APPEND` + `fsync`
- 出队 = 推进 cursor（仅推进，不原地删）
- Compaction：dequeue > 1000 且 file > 10 MB 时触发

**验收演练（仅对 Obsidian sink）**：60 s 内 30 次 `kill -9 daemon` · enqueue 端事件零丢失

### 3.5b 长期记忆 sink 降级策略对照（ADR-005）

| 维度 | 原 Hindsight sink（v2 设计） | 新 Supermemory sink（ADR-005, 已实施） |
|------|-----------------------------|----------------------------------------|
| 同步层 L0 | 实时 POST，2s 超时 | 实时 POST（urllib） |
| 重试层 L1 | 1s / 4s / 16s 三段重试 | **无重试** |
| 失败兜底 L2 | 推 `dlq.jsonl` | **不入 DLQ**，吞掉异常记 warning |
| 熔断层 L3 | 60s OPEN 熔断 | **无熔断** |
| Cursor 行为 | 失败不推进，下次 flush 重试 | **失败也推进**（best-effort，不阻塞主路径） |
| 实现代码 | 196 行 | ~80 行 |

**简化决策理由（必须留痕）**：
1. daemon 是 best-effort 二级 sink，**不是 source of truth**
2. Obsidian 是人类可读 source of truth 且本地 IO 无网络抖动，丢失风险已由 Obsidian sink 承接
3. Supermemory 三省六部其他通路（如 chat 直存）已是冗余写入路径
4. 丢一两条 Supermemory 不影响审计链路完整性
5. 若未来观测到丢失率不可接受，再补 pending 队列 / DLQ（YAGNI）

---

## 四、跨 profile 通知 — 转交方案

### 4.1 v2 不再做的事

- ❌ 不写 MEMORY.md 推送 Sink
- ❌ 不写 Session DB 反向回填 Sink
- ❌ 不在 EmpireThread 层做任何跨 profile fan-out

### 4.2 跨 profile 任务终态通知 — 走 kanban_notify_subs

**真实可用机制**（订阅方主动拉取，已是 v0.15.x 默认）：

```bash
# 场景：regent 想接收 engineer profile 完成的某任务通知到 telegram
hermes -p regent kanban notify-subscribe t_xxxxx \
    --platform telegram \
    --chat-id -5133970461
```

- 共享 kanban DB 是事件总线（`task_events` 表）
- regent 的 gateway watcher 自动每 5 s 拉取自己 owner 的订阅
- engineer 完成 → 写 `task_events`（kind=completed/blocked/...）→ regent 的 gateway 投递到群

**注意（W3 调研结论）**：

- v0.15.1 文档承诺的 `kanban.notification_sources: ['*']`（让 gateway 接受来自其他 profile 的订阅）**未在代码中实现**
- 但"接收方自建订阅"模式已足够覆盖 v1.0 Sink 1 的全部用例
- 若未来 `notification_sources` 配置真正落地（PR 中 / 后续版本），可作为"forward fan-out"补充能力使用

### 4.3 跨 profile 共享事实 — 走 kanban 任务体 + 评论

不再用 EmpireThread 推 MEMORY.md。共享事实统一走 kanban：

| 用例 | v1.0 走 EmpireThread | v2 走 kanban |
|------|--------------------|--------------|
| 决策记录广播 | Sink 1 push MEMORY.md | 创建任务 `assignee=regent, title=ADR-NNN, body=...` + 接收方订阅 |
| 跨 profile 状态同步 | Sink 4 推 Session DB | `kanban_comment` 写入共享 task 评论流 |
| boot-critical 事实 | Sink 1 写入 MEMORY.md | 接收方 profile 自己的 boot context tool 读取 kanban `assignee=<self>, status=open` 列表 |

→ 单一事件总线（kanban）+ 订阅方主动拉取，比 EmpireThread fan-out 简单一个数量级。

---

## 五、实施路线

| 周 | 内容 | 代码量 |
|:--:|------|:------:|
| W1 | `event_bridge/core.py`（无倒排索引）+ `cursor.py` + `daemon.py` + `sinks/obsidian.py` + Markdown 模板 + 单测 | ~160 行 |
| W2（ADR-005 修订） | `sinks/supermemory.py`（替换原 `sinks/hindsight.py`）+ HttpTransport（urllib）+ container_tag 映射 + contract test | ~80 行（原 Hindsight 设计为 140 行） |

**总计**：~240 行 Python（vs v1.0 的 ~550 行，**-56%**；vs v2 原 300 行进一步压缩 60 行）

**实施依赖**：

- W1 前置：确认 `~/Documents/Obsidian/AlexCai/` 目标子目录（已落地 `88_event-bridge/`）
- W2 前置（ADR-005 后）：
  - `SUPERMEMORY_API_KEY` 已在 `~/.hermes/.env`
  - container_tag 映射文件 `~/.hermes/supermemory.json`：regent → `hermes-cabinet`，default → `hermes`，其他 profile → fallback `hermes`
  - launchd plist wrap zsh：`/bin/zsh -c "source ~/.hermes/.env && exec python -m event_bridge.daemon"`

---

## 六、待决策项（v2 缩减）

1. **daemon 形态**：launchd sidecar（推荐，沿用 v1.0；已采用，wrap zsh 源 `~/.hermes/.env`）
2. **Obsidian 写入子目录**：`_event-bridge/`（设计推荐）vs `88_event-bridge/`（代码落地）— 已选 `88_event-bridge/`
3. ~~**MEMORY.md 策略**~~ — 已删除整个 Sink，无需决策
4. ~~**Session DB 重构**~~ — 已删除整个 Sink，无需决策
5. **`notification_sources` 是否提 PR 给上游 hermes-agent**：W3 调研发现文档与代码 gap，可作为社区贡献
6. ~~**Hindsight 接入参数**~~ — 整 sink 已替换为 Supermemory（ADR-005, 2026-05-30），无需决策
7. **Supermemory sink 是否升级为 pending 队列 + DLQ**：当前 best-effort 直发；若观测到丢失率不可接受再考虑（YAGNI）

---

## 七、与 v1.0 的差异速查

| 维度 | v1.0 | v2 |
|------|------|----|
| Sink 数 | 4 | 2 |
| 加固项 G | 5 | 3.5（G3 简化、G4 删除） |
| 总代码量 | ~550 行 | ~300 行 |
| 跨 profile 通知 | EmpireThread Sink 1 + 4 fan-out | kanban_notify_subs（接收方自建） |
| MEMORY.md 维护 | EventBridge 异步推送 | 让 hermes-cli 原生维护 |
| Session DB | 事件驱动重构 | 不动 |
| 实施周数 | 3 周 | 2 周 |

---

## 八、不建议做的事（沿用 v1.0 + 增补）

- ❌ 引入 Kafka / Redis Streams — 单用户过度工程
- ❌ 集中式 YAML/DSL 过滤规则 — 演变成上帝路由器
- ❌ Sink 间依赖编排 — 当前需求未到，留作未来事件链
- ❌ 同步 fan-out 到 pre_tool_call — 与 12-Factor F5/VI/IX 直接冲突
- ❌ **新增**：在 EmpireThread 层重复实现 kanban_notify_subs 已有的跨 profile 通知
- ❌ **新增**：依赖 `kanban.notification_sources` 配置直至上游代码侧实现（当前仅文档化）

---

## 九、变更记录

| 日期 | 变更 |
|------|------|
| 2026-05-30 | ADR-005 修订（决策依据 ARCH-TEST-001）：Sink 2「长期记忆写入」backend 由 Hindsight 替换为 Supermemory。删除 `sinks/hindsight.py`（-196 行）+ test_event_bridge_hindsight.py（-10 用例），新建 `sinks/supermemory.py`（~80 行，urllib best-effort 直发 `POST https://api.supermemory.ai/v3/documents`，camelCase payload）+ `test_event_bridge_supermemory.py`（+9 用例 S1-S9）。daemon 条件改 `SUPERMEMORY_API_KEY`；launchd plist wrap zsh 源 `~/.hermes/.env`。G5 队列/DLQ/熔断在 Supermemory sink 上简化为 best-effort（不入队、失败吞掉、cursor 仍推进）。当前 52 个 event_bridge 测试全绿，部署同步完成，已观测 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}`。 |
| 2026-05-29 | v2 缩窄版创建。W3 调研 hermes v0.15.x notification_sources 实际机制后，去掉 Sink 1 (MEMORY.md) + Sink 4 (Session DB)，G3 简化，G4 删除，~300 行 / 2 周 |
| 2026-05-28 | v1.0 创建（保留为 [[EmpireThread_事件桥_综合设计文档_v1.0]]） |
