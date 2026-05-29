# EmpireThread 事件桥 — v2 缩窄版

> **状态**：设计修订 ✅ | 实施待启动 ⏳
> **日期**：2026-05-29
> **修订背景**：W3 调研 hermes v0.15.x 跨 profile 通知机制（`kanban.notification_sources` / `kanban_notify_subs.notifier_profile`）后，对 v1.0 的 4-Sink 架构作"职责切割"
> **基线**：[[EmpireThread_事件桥_综合设计文档_v1.0]]
> **关联**：[[Hermes_路线图_v1.0]] · [[三省六部×A2A架构方案_20260529]] §11.1

---

## 一、为什么要修订

### 1.1 v1.0 的 4-Sink 与 v0.15.x 官方能力的重叠

| Sink | v1.0 用途 | v0.15.x 是否有官方替代 | 修订动作 |
|------|----------|----------------------|----------|
| **Sink 1: MEMORY.md** | 跨 profile boot-critical 事实推送 | ✅ `kanban_notify_subs` 跨 profile 投递（PR #28395 + 后续修复） | **去除** |
| **Sink 2: Obsidian** | 知识库文档（ADR / 事件日志）写入 | ❌ 官方无 | **保留** |
| **Sink 3: Hindsight** | 长期语义记忆写入 | ❌ 官方无 | **保留** |
| **Sink 4: Session DB** | 跨 profile 会话历史事件驱动 | ✅ 同 Sink 1（kanban 自身的 events + runs 表已是事件源） | **去除** |

**结论**：EmpireThread 必须存在的核心理由 = Obsidian + Hindsight 写入。其余两个 Sink 在 v0.15.x 已被官方原生能力覆盖。

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
2. **保留独有价值**：Obsidian 知识库写入 / Hindsight 语义记忆写入 — 官方无对应物
3. **G1-G5 加固项按风险面重估**：Sink 减半 → 部分加固降级
4. **代码量目标**：v1.0 ~550 行 → v2 ~300 行

---

## 二、v2 架构

### 2.1 数据流

```
事件源（A2A handler · kanban_event · pre_tool_call hook）
    │  hook 同步写入
    ▼
EmpireThread JSONL（已实现 ✅，~/Library/Application Support/EmpireThread/empire_thread.jsonl）
    │  fsevents + 1s 兜底轮询
    ▼
EventBridge daemon（launchd KeepAlive=true）
    ├── Sink: Obsidian          ← ADR / 事件日志 / 决策记录 md 文件
    └── Sink: Hindsight          ← REST PUT /memories（带降级）

跨 profile 任务完成通知 → 直接走 kanban_notify_subs（不经 EmpireThread）
跨 profile 共享事实        → 直接 commit 到共享 kanban + 接收方订阅
```

### 2.2 删除/保留对照

| v1.0 组件 | v2 状态 | 备注 |
|----------|---------|------|
| `event_bridge/core.py`（dispatch + 倒排索引） | ✅ 保留 | 倒排索引在 2-Sink 下可简化为直接遍历 |
| `event_bridge/cursor.py`（per-Sink cursor） | ✅ 保留 | 增量消费仍必要 |
| `event_bridge/daemon.py`（launchd sidecar） | ✅ 保留 | G1 异步约束未变 |
| `sinks/memory.py`（MEMORY.md flock + 原子 rename） | ❌ **删除** | 让位 kanban_notify_subs |
| `sinks/obsidian.py` | ✅ 保留 | 这是缩窄版核心 |
| `sinks/hindsight.py` | ✅ 保留 | 这是缩窄版核心 |
| `sinks/session.py`（Session DB 重构） | ❌ **删除** | kanban events/runs 表已是事件源 |
| `pending/`（per-Sink pending.jsonl） | ✅ 保留 | Hindsight 网络抖动需要 |
| `dlq/`（死信） | ✅ 保留 | Hindsight 真实风险 |

---

## 三、G1–G5 加固项重评

### 3.1 G1 — 异步 out-of-band Dispatch · **仍需**

**理由不变**：Hindsight 网络调用 P99 数百毫秒级，绝不能阻塞 pre_tool_call hook。launchd sidecar + fsevents + 1s 轮询的 v1.0 方案直接沿用。

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
| Hindsight | NEAR-ZERO（PUT API 不产生 hermes 事件） | 不必单独防御 |

**v2 方案**：

- ✅ **保留**：`_source = "sink_writeback"` 白名单（声明式，~10 行，防 Obsidian/Hindsight 未来扩展产生回路）
- ❌ **删除**：`threading.local` 重入计数（无 MEMORY.md 后无真实跨进程回路可拦）
- ❌ **删除**：触发熔断的 60 s OPEN（让位 G5 的 DLQ 单独管 Hindsight 重试）

**代码量**：v1.0 ~50 行 → v2 ~10 行

### 3.4 G4 — MEMORY.md `flock` + 原子 rename · **完全删除**

**整个 MEMORY.md Sink 移除后，G4 失去意义**。v1.0 设计中的 `fcntl.flock` + `tmp + os.replace` + SHA256 乐观锁三件套全部删除。

**回归路径**：MEMORY.md 由 hermes-cli 原生的 memory tool / boot context 机制单独维护，不再由 EmpireThread 异步写入。

**代码量**：v1.0 ~80 行 → v2 0 行

### 3.5 G5 — Bridge 队列强制落盘 · **保留**

**理由强化**：剩余两个 Sink 中，Hindsight 是网络 IO，是 v1.0 设计中 R3 风险（"Hindsight 网络雪崩"）的唯一承载点。pending.jsonl + DLQ + 三段重试是必须的。

**Obsidian 也走 pending 队列**：vault 写入虽然是本地，但走统一队列可统一恢复路径，无额外成本。

**沿用 v1.0 不变量**：

- 入队 = `O_APPEND` + `fsync`
- 出队 = 推进 cursor（仅推进，不原地删）
- Compaction：dequeue > 1000 且 file > 10 MB 时触发

**Hindsight 四层降级保留**：L0 实时(2s) → L1 重试(1/4/16s) → L2 DLQ → L3 熔断 60s

**验收演练保留**：60 s 内 30 次 `kill -9 daemon` · enqueue 端事件零丢失

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
| W2 | `sinks/hindsight.py` + 4 层降级 + `pending/` 队列 + DLQ + contract test | ~140 行 |

**总计**：~300 行 Python（vs v1.0 的 ~550 行，**-45%**）

**实施依赖**：

- W1 前置：确认 `~/Documents/Obsidian/AlexCai/` 目标子目录（推荐 `_event-bridge/`）
- W2 前置：确认 Hindsight 服务地址 + token + 4 层降级超时参数

---

## 六、待决策项（v2 缩减）

1. **daemon 形态**：launchd sidecar（推荐，沿用 v1.0）vs hook-wake
2. **Obsidian 写入子目录**：`_event-bridge/`（推荐）vs 现有知识库分类
3. ~~**MEMORY.md 策略**~~ — 已删除整个 Sink，无需决策
4. ~~**Session DB 重构**~~ — 已删除整个 Sink，无需决策
5. **`notification_sources` 是否提 PR 给上游 hermes-agent**：W3 调研发现文档与代码 gap，可作为社区贡献

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
| 2026-05-29 | v2 缩窄版创建。W3 调研 hermes v0.15.x notification_sources 实际机制后，去掉 Sink 1 (MEMORY.md) + Sink 4 (Session DB)，G3 简化，G4 删除，~300 行 / 2 周 |
| 2026-05-28 | v1.0 创建（保留为 [[EmpireThread_事件桥_综合设计文档_v1.0]]） |
