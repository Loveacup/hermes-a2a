# EmpireThread 事件桥 — 综合设计文档 v1.0

> **状态**：设计完成 ✅ | 已被 v2 缩窄版 + ADR-005（Hindsight → Supermemory）取代 ⚠️
> **日期**：2026-05-28（v1.0 原稿）· 2026-05-29（v2 缩窄）· 2026-05-30（ADR-005 Supermemory 替换）
> **产出**：CC 3 Agent 并行评估 + 5 加固项详细设计
> **后续修订**：
> - v2 缩窄版 [[EmpireThread_事件桥_v2_缩窄版]] 删除 MEMORY.md sink + Session DB sink
> - ADR-005 [[methodology#ADR-005]] 把"Hindsight sink"替换为"Supermemory sink"（单层架构）
> **关联**：[[Hermes_路线图_v1.0]] · [[methodology#ADR-005]]
>
> ⚠️ **本文档历史价值**：保留 v1.0 设计原貌（含 Hindsight 4 层降级方案）作为决策上下文留痕。**生产实施请以 [[EmpireThread_事件桥_v2_缩窄版]] + ADR-005 为准**。下文凡提及「Hindsight sink」的部分应替换理解为「Supermemory sink」，且：
> - HTTP 协议：`PUT /memories` → `POST https://api.supermemory.ai/v3/documents`
> - Payload：snake_case → camelCase（`containerTags` / `customId` / `metadata`）
> - 鉴权：`HINDSIGHT_API_KEY` → `SUPERMEMORY_API_KEY`
> - 4 层降级（L0 实时 / L1 重试 / L2 DLQ / L3 熔断）→ **已废除**，简化为 best-effort 直发，失败吞掉记 warning，不入队不重试不阻塞 cursor（理由见 ADR-005 §简化理由）

---

## 一、概述

### 1.1 问题

当前 EmpireThread（`empire_thread.py`，523 行，14 种事件类型）已实现事件写入 JSONL（append-only），但缺失**主动 fan-out**——A2A 任务完成、Kanban 操作发生后，事件写入 JSONL 就结束了。长期记忆（Supermemory）/ Obsidian / MEMORY.md / Session DB 完全不感知，记忆闭环断裂。

### 1.2 方案（v1.0 原稿；v2 / ADR-005 已收敛为 2 sink）

**Event Sourcing + Sink 插件架构**：在 EmpireThread JSONL 之上加一层 EventBridge，将事件主动分发到多个记忆系统。

```
事件源（Kanban, A2A, tool calls）
    │  pre_tool_call hook 写入
    ▼
EmpireThread JSONL（已实现 ✅）
    │  EventBridge daemon 异步消费
    ▼
EventBridge（新增）
    ├── Sink: MEMORY.md      ← boot-critical 事实（v2 已删除）
    ├── Sink: Obsidian        ← 知识库文档（ADR / 事件日志）
    ├── Sink: Hindsight       ← 长期语义记忆（ADR-005 已替换为 Supermemory）
    └── Sink: Session DB      ← 会话历史（v2 已删除）
```

**最终落地架构**（v2 + ADR-005，详见 [[EmpireThread_事件桥_v2_缩窄版]]）：

```
EventBridge
    ├── Sink: Obsidian        ← 知识库文档（pending 队列 + DLQ）
    └── Sink: Supermemory     ← POST /v3/documents（best-effort 直发，无降级）
```

### 1.3 设计原则

- **不改 Hermes core** — 纯 profile 层扩展
- **Sink 插件化** — 新增记忆系统只需实现 `supported_events / transform / write` 三方法
- **异步 out-of-band** — 禁止在 pre_tool_call hook 同步 fan-out
- **错误隔离** — 一个 Sink 失败不影响其他
- **幂等性** — event_id 去重，cursor 增量消费

---

## 二、CC 3 Agent 综合评估

### 2.1 评估结论

**✅ 方案通过，但须做 5 处关键加固。**

3 Agent（Architecture / Engineering / Risk）独立评审，全部给出"可执行"结论。

### 2.2 三方共识

1. **Sink 插件模式合身**：14 事件 × 4 记忆系统的稀疏矩阵天然适合插件化
2. **必须异步消费**：sidecar daemon + cursor 增量，禁止同步 dispatch
3. **cursor 文件 + 确定性 doc_id** 是幂等性最佳起点
4. **实现顺序 MEMORY → Obsidian → Hindsight → Session DB**（本地优先于外部）
5. **JSONL 当 WAL 已足够**，无需引入独立 WAL

### 2.3 5 处关键加固

| # | 加固项 | 不做会怎样 | v2 / ADR-005 状态 |
|---|--------|-----------|------------------|
| **G1** | dispatch 必须异步 out-of-band | 长期记忆 sink 抖动拖垮所有工具调用 +200ms | ✅ 保留 |
| **G2** | 事件类型倒排索引 + cursor 增量消费 | 10K 事件起 O(N²) 退化秒级阻塞 | ✅ 保留（倒排索引在 2-Sink 下简化为直接遍历） |
| **G3** | `_source` 白名单 + 重入计数 | Sink 写回被误识 → 指数级事件膨胀 | ⚠️ 仅保留白名单（重入计数随 MEMORY.md 删除） |
| **G4** | MEMORY.md `flock` + 原子 rename | boot context 被并发写空 | ❌ 整 sink 删除 |
| **G5** | 队列强制落盘 `pending.jsonl` | 崩溃丢事件，违反 12-Factor VI | ⚠️ 仅 Obsidian sink 走队列；Supermemory sink ADR-005 简化为 best-effort 直发 |

---

## 三、三方独立评审

### 3.1 架构维度（Worker-Architecture）

**Sink 插件模式适合，但需补"路由表"**：
- 强项：与 Hexagonal/Ports & Adapters 同构；错误隔离粒度恰当；增量演化零耦合
- 弱点：O(N×M) 调度需改为倒排索引 `dict[event_type, list[Sink]]`
- 过滤分两层：Bridge 层类型粗筛（O(1)）+ Sink 层语义细筛（`accept(event)`）
- 否决模式：Chain of Responsibility（语义错配）、Reactive Streams（心智负担）、Actor Model（过度工程）

**替代方案取舍**：
- ✅ 保留 Middleware Pipeline 作为 Bridge 内部 before/after hooks 层
- ✅ 保留 Stream cursor 概念作为增量消费抽象
- ❌ 不引入 Kafka / Redis Streams（杀鸡用牛刀）

### 3.2 工程维度（Worker-Engineering）

**实现顺序**（v1.0 原稿）：MEMORY（P0，本地优先）→ Obsidian（P1）→ Hindsight（P2，外部服务）→ Session DB（P3，重构）

**v2 + ADR-005 后实际实现顺序**：Obsidian → Supermemory（best-effort）

**三周迭代（v1.0 原稿；实际 v2 已压到 2 周，ADR-005 进一步压到 ~240 行）**：
- W1：Bridge 核心 + MEMORY Sink + 单测（~200 行）
- W2：Obsidian Sink + 集成测试（~150 行）
- W3：Hindsight Sink + 降级 + Session DB 改造（~200 行），合计 ~550 行

**5 个关键测试用例**（v1.0 原稿）：
1. `test_idempotent_replay` — 相同 event_id 不重复写入
2. `test_sink_isolation` — 一个 Sink 异常不阻塞其余
3. `test_event_type_filter` — 仅接收声明的事件类型
4. `test_malformed_jsonl_line_skipped` — 损坏行不阻塞
5. `test_hindsight_5xx_retry_then_dlq` — 3 次重试失败入死信（ADR-005 后已移除：Supermemory sink 无重试/无 DLQ）

**最终落地测试矩阵**：52 个 event_bridge 测试全绿，含 `test_event_bridge_supermemory.py` 的 S1-S9（详见 v2 缩窄版 §五 + ADR-005）

**性能**：10K 事件全量扫描 ~10ms（OK），100K 起强制 cursor 增量消费。1M 事件单次 tick < 5ms。

### 3.3 风险维度（Worker-Risk）

**Top 5 技术风险**：

| # | 风险 | 概率 | 影响 | 缓解 | v2 / ADR-005 状态 |
|---|------|:--:|:--:|------|------------------|
| R1 | Sink 自触发死循环 | H | H | `_source` 白名单 + threading.local 重入计数 >1 abort | ⚠️ 仅白名单保留 |
| R2 | MEMORY.md 并发写损坏 | H | H | `fcntl.flock` + tmp + `os.replace` 原子写 | ❌ 整 sink 删除 |
| R3 | Hindsight 网络雪崩 | M | H | 2s 硬超时 + circuit breaker（3 次失败 OPEN 60s） | ⚠️ Hindsight sink 已替换为 Supermemory；4 层降级删除，best-effort 直发（理由：daemon 是二级 sink，Obsidian 为 source of truth；详见 ADR-005） |
| R4 | JSONL 半行写入 | M | M | 推进前校验末字节 `\n`，否则回退 | ✅ 保留 |
| R5 | Sink 慢消费阻塞 | M | M | queue 满即落盘，主路径不阻塞 | ⚠️ Obsidian sink 保留；Supermemory sink 直发不入队 |

**v1.0 原 Hindsight 四层降级**：L0 实时(2s) → L1 重试队列(1/4/16s) → L2 DLQ → L3 熔断跳过(60s)

**ADR-005 替换后 Supermemory sink 降级 = 0 层**：失败吞掉记 warning，cursor 仍推进，best-effort（详见 v2 缩窄版 §3.5b 对照表）

**12-Factor 最大冲击**：III Config（统一 `config.yaml` + env 注入）和 VI Processes（队列强制落盘）

---

## 四、5 加固项详细设计

### 4.1 G1 — 异步 out-of-band Dispatch

**方案**：launchd sidecar 守护进程 + fsevents 监听 + 1s 兜底轮询

```
pre_tool_call hook → EmpireThread.append → JSONL fsync（hook 结束）
                                              │
                                   fsevents / 1s 轮询
                                              ▼
                              event_bridge_daemon（launchd KeepAlive=true）
                                   ├─ 读 cursor，增量消费
                                   ├─ Obsidian sink：enqueue pending.jsonl
                                   └─ Supermemory sink：直发 POST /v3/documents（ADR-005，不入队）
```

**文件**：`python -m event_bridge.daemon`（PYTHONPATH=`~/.hermes/plugins/hermes-a2a/`），`~/Library/LaunchAgents/com.hermes.eventbridge.plist`（wrap zsh 源 `~/.hermes/.env`）

**验收**：pre_tool_call p99 延迟启用 bridge 前后差 < 5ms

### 4.2 G2 — 倒排索引 + Cursor 增量消费

**两层**：
- Layer 1：`{EventType: [Sink]}` 倒排索引，O(1) 路由
- Layer 2：per-Sink `cursor.json`（`{last_event_id, lineno, byte_offset, inode, ts}`），原子 rename

**Cursor 推进单位**：行号 + byte_offset + inode（不用 timestamp，防 NTP 回拨）

```python
def consume_for(self, sink):
    cur = CursorStore.load(sink.name)
    st = os.stat(JSONL_PATH)
    if cur.inode != st.st_ino: cur = Cursor("",0,0,st.st_ino,0.0)  # rotate → cold start
    with open(JSONL_PATH, "rb") as f:
        f.seek(cur.byte_offset)
        for raw in f:
            if not raw.endswith(b"\n"): break    # 半行：留下次
            evt = json.loads(raw)
            if sink in self.sinks_for(evt["type"]) and sink.accept(evt):
                enqueue(sink, evt)
            cur.lineno += 1; cur.byte_offset = f.tell()
        CursorStore.save_atomic(sink.name, cur)
```

**性能门禁**：1M 事件、100 条增量 tick < 5ms

### 4.3 G3 — `_source` 白名单 + 重入计数

**两层防线**：
1. **声明式**：Sink 回灌事件必须带 `content._source = "sink_writeback"`，dispatch 入口看到直接 return
2. **运行时**：`threading.local` 维护重入深度 > 1 立即 abort + 写 ERROR 事件 + 触发 60s 熔断

```python
class DispatchGuard:
    WHITELIST = frozenset({"sink_writeback", "bridge_internal", "dlq_replay"})
    
    def check(event):
        if event["content"].get("_source") in WHITELIST: return "skip"
        if BREAKER.is_open(): return "skip_breaker"
    
    def enter():
        depth = _local.depth = getattr(_local, "depth", 0) + 1
        if depth > 1:
            BREAKER.open_for(60)
            return False
        return True
```

**为什么两层都要**：单白名单怕 Sink 忘记打标；单计数拦不住跨进程回路。

### 4.4 G4 — MEMORY.md `flock` + 原子 rename

**三件套**：互斥（flock）+ 原子（tmp + os.replace）+ 乐观锁（SHA256 校验）

```python
def _write_once(self, events):
    with open(LOCK, "rb+") as lock_fp:
        fcntl.flock(lock_fp, LOCK_EX)       # 互斥
        old = MEMORY.read_bytes()
        sha_before = hashlib.sha256(old).hexdigest()
        new = self._merge(old.decode(), events)
        tmp = MEMORY.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_bytes(new.encode()); os.fsync(tmp)
        # 乐观锁
        sha_now = hashlib.sha256(MEMORY.read_bytes()).hexdigest()
        if sha_now != sha_before: raise OptimisticLockError
        os.replace(tmp, MEMORY)              # 原子 rename
        os.fsync(os.open(MEMORY.parent, os.O_DIRECTORY))
```

**合并策略**：增量 patch，按 section（`## Recent Activity` / `## Recent Decisions`）追加 bullet，trim 到 20 条。**全量重写已否决**。

### 4.5 G5 — Bridge 队列强制落盘

**核心**：每 Sink 独立 `pending.jsonl`，daemon 主线程 enqueue（append + fsync），Sink worker 线程 dequeue（推进 cursor）。

**不变量**：
- 入队 = `O_APPEND` + `fsync`
- 出队 = 推进 cursor（仅推进，不原地删）
- Compaction：dequeue > 1000 且 file > 10MB 时触发

```
EventBridge.dispatch → PendingQueue.enqueue(fsync) → return < 5ms
SinkWorker: peek_next → sink.write → advance
            retriable error: 不前进
            fatal error: advance_to_dlq
```

**QueueOverflow 处理**：(1) 丢弃 AUDIT，保留 DECISION/ERROR；(2) 写 `_source=bridge_internal` 的 ERROR 事件

**验收演练**：60s 内 30 次 `kill -9 daemon`，enqueue 端事件零丢失。

---

## 五、实施路线

| 周 | 内容 | 代码量 |
|:--:|------|:------:|
| W1 | `event_bridge/core.py` + `cursor.py` + `sinks/memory.py` + `daemon.py` + 单测 | ~200 行 |
| W2 | `sinks/obsidian.py` + Markdown 模板 + 集成测试 | ~150 行 |
| W3 | `sinks/hindsight.py` + 降级 + `sinks/session.py` + contract test | ~200 行 |

**总计**：~550 行 Python，无外部依赖，不改 Hermes core。

---

## 六、待决策项

1. **daemon 形态**：launchd sidecar（低延迟 <100ms，推荐）vs hook-wake（低资源占用）
2. **Obsidian 写入子目录**：`_event-bridge/`（隔离，推荐）vs 直接归入现有知识库
3. **MEMORY.md 策略**：增量 patch（按 section，推荐）vs 全量重写
4. **路线图衔接**：W3 是否顺手做审计全闭环的 `processed` 表（+80 行）

---

## 七、不建议做的事

- ❌ 引入 Kafka / Redis Streams — 单用户过度工程
- ❌ 集中式 YAML/DSL 过滤规则 — 演变成上帝路由器
- ❌ Sink 间依赖编排 — 当前需求未到，留作未来事件链
- ❌ 同步 fan-out 到 pre_tool_call — 与 12-Factor F5/VI/IX 直接冲突

---

## 八、变更记录

| 日期 | 变更 |
|------|------|
| 2026-05-28 | 初始创建。合并 CC 3 Agent 评估报告 + 5 加固项设计 + 网络调研结论 |
| 2026-05-29 | 后续被 v2 缩窄版取代（删 MEMORY.md + Session DB），参见 [[EmpireThread_事件桥_v2_缩窄版]] |
| 2026-05-30 | ADR-005（决策依据 ARCH-TEST-001）：第三个 sink「Hindsight」整体替换为「Supermemory」。本文档内所有提到 Hindsight 的段落需对照顶部 §0 注释理解（HTTP/Payload/鉴权/降级全部变更），生产实施请以 v2 缩窄版 + ADR-005 为准。 |
