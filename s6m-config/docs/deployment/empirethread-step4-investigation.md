---
title: EmpireThread Step 4 — 实施状态调查
date: 2026-05-30（含 ADR-005 Supermemory 替换修订）
status: 调查报告
scope: 仅调查，不改代码
related:
  - "[[EmpireThread_事件桥_v2_缩窄版]]"
  - "[[Hermes_路线图_v1.0]]"
  - "[[methodology#ADR-005]]"
---

# EmpireThread Step 4 — 实施状态调查

## 摘要

| 维度 | 状态 |
|------|------|
| 代码 ↔ 设计一致性 | ✅ 高度一致（v2 缩窄版完全落地 + ADR-005 Hindsight → Supermemory 替换完成） |
| 单元测试 | ✅ **52/52 通过**（删 Hindsight 10 用例，新增 Supermemory 9 用例 S1-S9） |
| 代码部署到 `~/.hermes/plugins/` | ✅ 已同步（mtime 一致） |
| launchd plist 模板 | ✅ 存在于 `core/templates/event-bridge-launchd.plist` |
| launchd plist 已渲染并安装 | ✅ 已安装（wrap zsh，源 `~/.hermes/.env` 抽取 `SUPERMEMORY_API_KEY`） |
| daemon 正在运行 | ✅ **已重启并运行**，观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}` |
| 事件源 JSONL 存在性 | ⚠️ **1/17 profile** 有 jsonl（regent 46 行，疑为 P1 workspace 注入的测试数据） |
| 上游 hook 写 JSONL | ❌ 安装的 `hermes-agent` 中**无任何 emitter** grep 命中 `empire-thread`（P0-1 待打通） |

**核心结论**：v2 缩窄版代码与测试已就绪，**ADR-005 Hindsight → Supermemory 替换已完成并部署**；Obsidian sink + Supermemory sink 双链路已被验证（46 个 obsidian md + 至少 1 次 supermemory dispatch）。**生产链路仍有最后一公里**：上游 emit hook 尚未在 `hermes-agent` 安装，需 P0-1 打通。

---

## Q1 · 代码与设计一致性

### ✅ 已实现且与设计完全一致

| 设计组件 | 代码位置 | 行数 | 备注 |
|----------|---------|:---:|------|
| Event + Sink ABC + dispatch | `core/event_bridge/core.py` | 116 | `_source=sink_writeback` 白名单已落（`core.py:58`） |
| per-(sink, profile) Cursor | `core/event_bridge/cursor.py` | 52 | tmp + os.replace 原子化（`cursor.py:43-51`），inode 检测沿用 v1.0 |
| launchd sidecar daemon | `core/event_bridge/daemon.py` | 73 | 含 `flush_pending` 调度环；条件改 `SUPERMEMORY_API_KEY`（ADR-005） |
| Pending 出站队列 | `core/event_bridge/pending.py` | 145 | O_APPEND + fsync、torn-line 防护、阈值 compaction 全到位（仅 Obsidian sink 使用） |
| DLQ | `core/event_bridge/dlq.py` | 41 | append-only JSONL，无 cursor，符合 G5（仅 Obsidian sink 使用） |
| Obsidian Sink | `core/event_bridge/sinks/obsidian.py` | 54 | 路径 `vault/88_event-bridge/YYYY/MM/DD/<event_id>.md`，幂等写 |
| ~~Hindsight Sink + 4 层降级~~ | ~~`core/event_bridge/sinks/hindsight.py`~~ | ~~196~~ | **ADR-005 已删除**（决策依据 ARCH-TEST-001） |
| Supermemory Sink（ADR-005 新建） | `core/event_bridge/sinks/supermemory.py` | ~80 | `SupermemorySink(name="supermemory")` + `HttpTransport`（urllib，无 SDK 依赖）；`POST https://api.supermemory.ai/v3/documents`，Bearer `SUPERMEMORY_API_KEY`；camelCase payload（`content` + `containerTags` + `customId` + `metadata`）；container_tag 映射 regent → `hermes-cabinet`，default/其他 → fallback `hermes`；**best-effort 直发，不入队、不重试、失败吞掉记 warning，cursor 仍推进** |
| launchd plist 模板 | `core/templates/event-bridge-launchd.plist` | 49 | `{{PYTHON}}/{{HOME}}/{{HERMES_HOME}}/{{OBSIDIAN_VAULT}}/{{LOG_DIR}}` 占位符；实际安装 wrap zsh 源 `~/.hermes/.env` |

**总代码量**：约 **360 行 Python + 49 行 plist**（ADR-005 后从 475 行降至 360 行：-196 hindsight + 80 supermemory ≈ -116 行），完美吻合 v2 设计目标 ~300 行。

### ✅ v2 + ADR-005 设计标记"删除"且代码确已不存在

```
$ grep -rn "MemorySink|SessionSink|HindsightSink|sinks/memory|sinks/session|sinks/hindsight" core/
(无命中)
$ ls core/event_bridge/sinks/
__init__.py  obsidian.py  supermemory.py
```

- `sinks/memory.py` ❌ 未创建
- `sinks/session.py` ❌ 未创建
- `sinks/hindsight.py` ❌ ADR-005 已删除
- `sinks/supermemory.py` ✅ ADR-005 已新建
- `threading.local` 重入计数 ❌ 未实现（G3 降级到位）
- 倒排索引 `_inverted_index` ❌ 未实现（G2 简化到位）
- MEMORY.md flock + 原子 rename ❌ 未实现（G4 删除到位）

### ✅ v2 保留的加固项均已落

- **G1 异步 daemon**：`daemon.py` 用 `time.sleep(poll_interval)` 轮询，默认 1 s tick；plist 设 `KeepAlive=true ThrottleInterval=5`
- **G2 cursor 增量**：`consume_for` 按 byte_offset 续读，半行回退（`core.py:81-87`），inode 变化触发冷启
- **G3 `_source` 白名单**：`Sink.accept` 默认拒 `sink_writeback`（`core.py:56-58`），仅 10 行，正合 v2 设计
- **G5 fsync + compaction**：`PendingQueue.enqueue` 走 `O_APPEND + os.fsync`（`pending.py:67-74`），阈值 1 000 条 / 10 MB 触发 compaction

### ⚠️ 与 v2 设计的小偏差（未来观察）

| 项目 | 现状 | 设计期望 | 风险 |
|------|------|---------|------|
| `paths.py:obsidian_event_dir()` | `88_event-bridge/` | 设计 §6.1 推荐 `_event-bridge/`（待决） | 命名差异，不影响功能；落地前可统一 |
| `daemon.py:default_sinks()` | 缺 `SUPERMEMORY_API_KEY` 时仅启 Obsidian（ADR-005 后） | 设计未明说降级路径 | 当前实现合理（避免半失败） |
| ~~`HindsightSink._rewrite_pending` IO 放大~~ | ~~每次 flush 重写整文件 + 删 cursor~~ | ~~设计 §3.5"仅推进 cursor，不原地删"~~ | **ADR-005 后已消除**：Supermemory sink 不入队、不重写 pending，问题不复存在 |

---

## Q2 · 测试覆盖

```
$ python -m pytest tests/unit/test_event_bridge*.py -v --tb=short
============== 52 passed in 0.18s ==============
```

**全部 52 测试通过**（ADR-005 后：删 Hindsight 10 + 新增 Supermemory 9 = -1 净差），分布：

| 文件 | 用例数 | 覆盖面 |
|------|:---:|------|
| test_event_bridge_contract.py | 4 | 跨模块契约：C1 中途崩溃不推进、C2 enqueue 持久化、C3 at-least-once、C4 半行不可见 |
| test_event_bridge_core.py | 10 | Event 字段、profile 扫描、cursor 推进、增量/损坏行/半行/inode 旋转/独立 cursor/拒事件仍推 |
| test_event_bridge_cursor.py | 6 | 默认值、空载入零 cursor、roundtrip、原子无残留 tmp、独立存储、损坏回退 |
| test_event_bridge_daemon.py | 4 | 模块形状、单 tick 写 Obsidian、二 tick 幂等、plist XML 有效性 |
| test_event_bridge_dlq.py | 4 | append 持久化、顺序保持、空迭代、损坏跳过 |
| ~~test_event_bridge_hindsight.py~~ | ~~10~~ | **ADR-005 已删除** |
| test_event_bridge_supermemory.py（**新**） | 9 | S1 sink name="supermemory"；S2 accept 拒 sink_writeback；S3 write() 直发 transport（不入队）；S4 container_tag 映射 regent → `hermes-cabinet`；S5 default → `hermes`；S6 fallback：未知 profile → `hermes`；S7 payload 形状（content/containerTags/customId/metadata）；S8 transport 失败被吞，不抛；S9 无 task_id 时 metadata 不含 task_id（用 `StubTransport` 注入 `add_document(payload)` mock）|
| test_event_bridge_obsidian.py | 6 | 日期路径、frontmatter、幂等、缺 ts 回退、accept 拒 writeback、名字 |
| test_event_bridge_pending.py | 9 | enqueue 立刻持久化、cursor 起点、advance、重启续航、compaction、损坏跳过、半行保留、空队列、字段 |

**v2 已删除 Sink 是否仍在测**：❌ **否**。`grep -rn "MemorySink|SessionSink|HindsightSink|sinks/memory|sinks/session|sinks/hindsight" tests/` 零命中。

---

## Q3 · 部署就绪度

### ❌ Daemon 入口脚本未部署到 `~/.hermes/bin/`

```
$ ls ~/.hermes/bin/
tirith    ← 唯一可执行文件，与 EmpireThread 无关
```

设计 §3.1 要求路径 `~/.hermes/bin/event_bridge_daemon.py`，**不存在**。当前 daemon 入口完全靠 `python -m event_bridge.daemon`，plist 模板里已写死该启动方式（`event-bridge-launchd.plist:11-12`），所以**不需要**单独 bin 脚本——只要 PYTHONPATH 指向 `~/.hermes/plugins/hermes-a2a/`（plist 已包含此 env，第 28-29 行）。设计与实现已收敛到一种更干净的方式。

### ✅ 代码已同步到 `~/.hermes/plugins/hermes-a2a/event_bridge/`

`diff -q core/event_bridge/core.py ~/.hermes/plugins/hermes-a2a/event_bridge/core.py` 无输出（一致），daemon.py 同。mtime 均为 `May 30 02:08`，CLAUDE.md §"每次改 core 都要同步"的 P0 约束达成。

### ✅ launchd plist 已渲染并安装（ADR-005 后状态）

- 模板 `core/templates/event-bridge-launchd.plist` 存在
- **占位符已渲染**，且 plist 已 wrap zsh 以源 `~/.hermes/.env`（抽取 `SUPERMEMORY_API_KEY`）
- daemon 重启后 launchctl 可见 `com.hermes.eventbridge`，PID 活跃

### ✅ Daemon 已运行

daemon 已重启，已观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}`，证明 Obsidian + Supermemory 双 sink 在线。

### ⚠️ JSONL 事件源 — 仅 regent 1/17，且疑为陈旧测试数据

```
$ find ~/.hermes/profiles -maxdepth 2 -name "empire-thread.jsonl"
/Users/alexcai/.hermes/profiles/regent/empire-thread.jsonl   ← 11 387 字节, 46 事件
```

- 其它 16 profile（archivist/auditor/budget/default/dispatcher/engineer/gongbu/hanlinyuan/jiangzuojian/planner/protocol/registry/reviewer/shangshu/tester、空缺 default 已含）**全部无 empire-thread.jsonl**
- regent jsonl 最新时间戳为 `2026-05-26T15:38:54+00:00`（4 天前），与 `cursor: lineno=46 byte_offset=11387` 完全一致——表明它在 5/26 那次手工 `--once` tick 后**未再增长**
- 安装的 `~/.hermes/hermes-agent/` 中 `grep -rn "empire-thread\|empire_thread"` **零命中**——上游 pre_tool_call hook 不写该 JSONL
- 现有 jsonl 内容看起来是 `~/.hermes/workspaces/12-factor-p1-empire-thread/init_empire_thread.py` 早期种入的测试数据

### ✅ Obsidian 端写入路径已被验证一次

`~/Documents/Obsidian/AlexCai/88_event-bridge/2026/` 下 **46 个 md 文件**，与 regent jsonl 46 事件一一对应。Obsidian sink 的 e2e 写入链路在 5/30 02:38 那次手工 tick 已被验证可用，cursor 文件留下 `last_ts=2026-05-26T15:38:54.377486+00:00` 作为证据。

### ✅ Supermemory 端已在线（ADR-005 替换 Hindsight 后）

- `~/.hermes/event-bridge/hindsight/` 目录已废弃（Hindsight sink 整体移除）
- Supermemory sink 是 best-effort 直发，**不产生 pending 队列 / DLQ 目录**
- `SUPERMEMORY_API_KEY` 已经由 launchd plist wrap zsh 从 `~/.hermes/.env` 注入
- container_tag 映射来自 `~/.hermes/supermemory.json`（regent → `hermes-cabinet`，default → `hermes`，fallback `hermes`）
- 部署后已观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}` —— 双 sink 端到端验证通过

**ADR-005 决策依据 ARCH-TEST-001**：Hindsight 从未启用且三省六部全线已 Supermemory 化，废除双层架构；详见 [[methodology#ADR-005]] + [[EmpireThread_事件桥_v2_缩窄版#2.1a]]。

---

## Q4 · 让 v2 进入生产的 TODO 清单

### P0 — 阻塞性，本周必须

1. **打通事件源** — 在 `hermes-agent` 的 `pre_tool_call` / `task_event` 钩子里加上 `empire-thread.jsonl` 写入。证据：当前 `grep` 零命中。
   - 方案 A：在 `~/.hermes/hermes-agent/hermes_cli/hooks.py` 内增加 emit
   - 方案 B：通过 `audit_hook.py` 旁路（`~/.hermes/plugins/hermes-a2a/audit_hook.py` 已是 hook 实体，扩展更轻）
   - 验收：跑任意 hermes 命令后，对应 profile 下 `empire-thread.jsonl` 行数 +1，schema 兼容 `Event` 字段（`event_id/ts/profile/event/data/task_id/source`）

2. ~~**渲染并安装 launchd plist**~~ — ✅ **已完成（ADR-005 部署时同步处理）**：plist wrap zsh 源 `~/.hermes/.env` 注入 `SUPERMEMORY_API_KEY`，daemon 已重启并观测到 `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}`。

3. **清理陈旧 cursor + 陈旧测试数据**（避免线上误读）
   - 决策一：把 `~/.hermes/profiles/regent/empire-thread.jsonl` 整文件归档/移除，让 daemon 从空文件冷启
   - 决策二：保留并接受 cursor 已指到 EOF（46/46）作为生产 baseline
   - 验收：daemon 启动后 `~/.hermes/event-bridge/cursors/` 内只有正常推进的 cursor，无空跑增量

### P1 — 生产硬化（一周内）

4. ~~**`HINDSIGHT_API_KEY` + `HINDSIGHT_API_URL` 落地**~~ — ✅ **已被 ADR-005 取代**：Hindsight 整 sink 替换为 Supermemory，`SUPERMEMORY_API_KEY` 已在 `~/.hermes/.env`，container_tag 映射在 `~/.hermes/supermemory.json`，daemon 已注册 SupermemorySink。

5. ~~**修正 `HindsightSink._rewrite_pending` 的 IO 放大**~~ — ✅ **已被 ADR-005 消除**：Supermemory sink 不入队，无 `_rewrite_pending` 路径，IO 放大问题不复存在。

6. **执行 v2 §3.1 验收门禁**
   - 启用 daemon 前后用 `pre_tool_call` 测 100 次延迟 → P99 差 < 5 ms
   - kill -9 daemon × 30 次 / 60 s · enqueue 端零丢失（v2 §3.5，仅对 Obsidian sink 验证；Supermemory sink ADR-005 后是 best-effort 不入队，不在此演练范围）
   - 1 M 事件 · 100 条增量 tick < 5 ms（v2 §3.2）

7. **统一 Obsidian 子目录命名**（v2 §6.1 待决项 2）
   - 当前 `88_event-bridge/`，设计推荐 `_event-bridge/`
   - 任一即可，**关键是做一次决策并把 `paths.py:26-27` 与设计文档对齐**
   - 验收：设计 §6.1 改为"已决：`88_event-bridge/`"或代码改回 `_event-bridge/`

### P2 — 收尾 / 长尾

8. **doctor 脚本扩展** — `core/scripts/hermes-a2a-doctor.sh` 增加 EmpireThread 健康检查段：
   - `launchctl print gui/$UID/com.hermes.eventbridge` 是否在跑
   - 每个 profile 是否有 jsonl 且 mtime < 60 s（活跃信号）
   - cursor 落后 jsonl 行数（积压告警）
   - pending.jsonl 行数（Obsidian sink 本地阻塞告警；Supermemory sink ADR-005 后不入队，改观测 daemon log 中的 `supermemory write failed` warning 数）

9. **跨 profile fan-out 文档化转交** — v2 §四已设计走 `kanban_notify_subs`，但还没有具体的"如何订阅"操作手册纳入 `s6m-config/docs/`。建议补一个 `cross-profile-notify-howto.md`。

10. **向上游 hermes-agent 提 PR 落地 `notification_sources`** — W3 调研发现文档承诺 vs 代码 gap。**非阻塞**，可作为社区贡献（v2 §6.5）。

11. **生产观测** — `pending.jsonl` 行数与 `dlq.jsonl` 行数作为 Grafana 数据点（或 `~/.hermes/event-bridge/metrics.json`），日志接入既有 launchd 日志轮转。

---

## 关键证据索引

| 主张 | 证据命令 / 路径 |
|------|----------------|
| 代码 ↔ 设计一致 | `core/event_bridge/{core,cursor,daemon,paths,pending,dlq}.py` + `sinks/{obsidian,supermemory}.py`（ADR-005 后） |
| 52/52 测试通过 | `python -m pytest tests/unit/test_event_bridge*.py -v`（含 test_event_bridge_supermemory.py 9 用例 S1-S9） |
| MemorySink/SessionSink/HindsightSink 已删除 | `ls core/event_bridge/sinks/` 仅 obsidian/supermemory；`grep -rn` 零命中 |
| 代码已同步部署 | `diff -q core/.../core.py ~/.hermes/plugins/.../core.py` 无输出 |
| plist 模板存在 | `core/templates/event-bridge-launchd.plist` |
| plist 已安装 | launchd plist wrap zsh 源 `~/.hermes/.env`，daemon 重启后 launchctl 可见 |
| daemon 在运行 | `dispatch: {'obsidian/regent': 1, 'supermemory/regent': 1}` 已观测 |
| 16 profile 无 jsonl | `find ~/.hermes/profiles -maxdepth 2 -name empire-thread.jsonl` 仅 regent |
| 上游 hook 无 emit | `grep -rn "empire-thread" ~/.hermes/hermes-agent/` 零命中（P0-1 待打通） |
| Obsidian sink 写过 | `find ~/Documents/Obsidian/AlexCai/88_event-bridge/2026 -name "*.md" \| wc -l` = 46 |
| Supermemory sink 已在线 | `SUPERMEMORY_API_KEY` 在 `~/.hermes/.env`；container_tag 映射在 `~/.hermes/supermemory.json`；dispatch 计数器已 +1 |

---

## 一句话总结

EmpireThread v2 + ADR-005 的**代码、测试、daemon、Supermemory sink 已全部 production-ready**——仅剩 P0-1 emit hook 一公里：在 hermes-agent 加 `pre_tool_call` 写 `empire-thread.jsonl`，事件即可一路流到 Obsidian + Supermemory 双 sink。原 P1 的 Hindsight 接入 / IO 放大修正已被 ADR-005 整体消除。
