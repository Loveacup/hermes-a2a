---
title: EmpireThread Step 4 — 实施状态调查
date: 2026-05-30
status: 调查报告
scope: 仅调查，不改代码
related:
  - "[[EmpireThread_事件桥_v2_缩窄版]]"
  - "[[Hermes_路线图_v1.0]]"
---

# EmpireThread Step 4 — 实施状态调查

## 摘要

| 维度 | 状态 |
|------|------|
| 代码 ↔ 设计一致性 | ✅ 高度一致（v2 缩窄版完全落地） |
| 单元测试 | ✅ **53/53 通过**（0.19 s） |
| 代码部署到 `~/.hermes/plugins/` | ✅ 已同步（mtime 一致） |
| launchd plist 模板 | ✅ 存在于 `core/templates/event-bridge-launchd.plist` |
| launchd plist 已渲染并安装 | ❌ **未安装**到 `~/Library/LaunchAgents/` |
| daemon 正在运行 | ❌ **未运行**（launchctl 无 `com.hermes.eventbridge`） |
| 事件源 JSONL 存在性 | ⚠️ **1/17 profile** 有 jsonl（regent 46 行，疑为 P1 workspace 注入的测试数据） |
| 上游 hook 写 JSONL | ❌ 安装的 `hermes-agent` 中**无任何 emitter** grep 命中 `empire-thread` |

**核心结论**：代码与测试已就绪，**生产链路完全未打通**——既无 daemon 守护，也无事件源 emitter。下游 Obsidian sink 写文件能力已被一次性手工 tick 验证（46 个 md 文件 + 1 个 cursor 文件），Hindsight sink 因缺 `HINDSIGHT_API_KEY` 从未被实例化。

---

## Q1 · 代码与设计一致性

### ✅ 已实现且与设计完全一致

| 设计组件 | 代码位置 | 行数 | 备注 |
|----------|---------|:---:|------|
| Event + Sink ABC + dispatch | `core/event_bridge/core.py` | 116 | `_source=sink_writeback` 白名单已落（`core.py:58`） |
| per-(sink, profile) Cursor | `core/event_bridge/cursor.py` | 52 | tmp + os.replace 原子化（`cursor.py:43-51`），inode 检测沿用 v1.0 |
| launchd sidecar daemon | `core/event_bridge/daemon.py` | 73 | 含 `flush_pending` 调度环 |
| Pending 出站队列 | `core/event_bridge/pending.py` | 145 | O_APPEND + fsync、torn-line 防护、阈值 compaction 全到位 |
| DLQ | `core/event_bridge/dlq.py` | 41 | append-only JSONL，无 cursor，符合 G5 |
| Obsidian Sink | `core/event_bridge/sinks/obsidian.py` | 54 | 路径 `vault/88_event-bridge/YYYY/MM/DD/<event_id>.md`，幂等写 |
| Hindsight Sink + 4 层降级 | `core/event_bridge/sinks/hindsight.py` | 196 | L0(2s) → L1(1/4/16s) → L2 DLQ → L3 熔断 60 s，clock+transport 可注入 |
| launchd plist 模板 | `core/templates/event-bridge-launchd.plist` | 49 | `{{PYTHON}}/{{HOME}}/{{HERMES_HOME}}/{{OBSIDIAN_VAULT}}/{{LOG_DIR}}` 占位符 |

**总代码量**：约 **475 行 Python + 49 行 plist**，对比 v2 设计目标 ~300 行偏高，溢出主要来自 hindsight.py 的注入点和 pending.py 的 torn-line 防护——属于工程必要复杂度。

### ✅ v2 设计标记"删除"且代码确已不存在

```
$ grep -rn "MemorySink|SessionSink|sinks/memory|sinks/session" core/
(无命中)
$ ls core/event_bridge/sinks/
__init__.py  hindsight.py  obsidian.py
```

- `sinks/memory.py` ❌ 未创建
- `sinks/session.py` ❌ 未创建
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
| `daemon.py:default_sinks()` | 缺 `HINDSIGHT_API_KEY` 时仅启 Obsidian | 设计未明说降级路径 | 当前实现合理（避免半失败） |
| `HindsightSink._rewrite_pending` | 每次 flush 重写整文件 + 删 cursor | 设计 §3.5"仅推进 cursor，不原地删" | **潜在偏离**：对成功项也走重写而非纯 advance，遇到 retry 队列长时 IO 放大；建议改成"成功 → advance；DLQ/retry → 异步 compaction" |

---

## Q2 · 测试覆盖

```
$ python -m pytest tests/unit/test_event_bridge*.py -v --tb=short
============== 53 passed in 0.19s ==============
```

**全部 53 测试通过**，分布：

| 文件 | 用例数 | 覆盖面 |
|------|:---:|------|
| test_event_bridge_contract.py | 4 | 跨模块契约：C1 中途崩溃不推进、C2 enqueue 持久化、C3 at-least-once、C4 半行不可见 |
| test_event_bridge_core.py | 10 | Event 字段、profile 扫描、cursor 推进、增量/损坏行/半行/inode 旋转/独立 cursor/拒事件仍推 |
| test_event_bridge_cursor.py | 6 | 默认值、空载入零 cursor、roundtrip、原子无残留 tmp、独立存储、损坏回退 |
| test_event_bridge_daemon.py | 4 | 模块形状、单 tick 写 Obsidian、二 tick 幂等、plist XML 有效性 |
| test_event_bridge_dlq.py | 4 | append 持久化、顺序保持、空迭代、损坏跳过 |
| test_event_bridge_hindsight.py | 10 | 名字、accept 拒 writeback、write 仅入队、flush 成功推进、失败保留、重试间隔、用尽进 DLQ、熔断、成功重置、payload 结构 |
| test_event_bridge_obsidian.py | 6 | 日期路径、frontmatter、幂等、缺 ts 回退、accept 拒 writeback、名字 |
| test_event_bridge_pending.py | 9 | enqueue 立刻持久化、cursor 起点、advance、重启续航、compaction、损坏跳过、半行保留、空队列、字段 |

**v2 已删除 Sink 是否仍在测**：❌ **否**。`grep -rn "MemorySink|SessionSink|sinks/memory|sinks/session" tests/` 零命中。`test_event_bridge_hindsight.py` 中出现的 `memory` 字样均为 Hindsight REST API 的 `put_memory` 方法名，非旧 MemorySink。

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

### ❌ launchd plist 未渲染并安装

```
$ ls ~/Library/LaunchAgents/ | grep -i event
(无输出)

$ HOME=/Users/alexcai launchctl list | grep -i 'hermes.event\|empire\|bridge'
(无 com.hermes.eventbridge 命中)
```

- 模板 `core/templates/event-bridge-launchd.plist` 存在
- **占位符 `{{PYTHON}}/{{HOME}}/{{HERMES_HOME}}/{{OBSIDIAN_VAULT}}/{{LOG_DIR}}/{{WORKING_DIR}}` 从未被渲染**
- 渲染脚本（参照 `scripts/seed-a2a-symlinks.sh` 风格）**尚不存在**

### ❌ Daemon 未运行

无 PID，无日志（`~/.hermes/logs/eventbridge.{out,err}.log` 不存在）。

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

### ⚠️ Hindsight 端从未运行

`~/.hermes/event-bridge/hindsight/` 目录为空（无 `pending.jsonl`、无 `dlq.jsonl`）。`HINDSIGHT_API_KEY` 未在环境中设置——`default_sinks()` 直接跳过 HindsightSink 实例化（`daemon.py:28-32`）。

---

## Q4 · 让 v2 进入生产的 TODO 清单

### P0 — 阻塞性，本周必须

1. **打通事件源** — 在 `hermes-agent` 的 `pre_tool_call` / `task_event` 钩子里加上 `empire-thread.jsonl` 写入。证据：当前 `grep` 零命中。
   - 方案 A：在 `~/.hermes/hermes-agent/hermes_cli/hooks.py` 内增加 emit
   - 方案 B：通过 `audit_hook.py` 旁路（`~/.hermes/plugins/hermes-a2a/audit_hook.py` 已是 hook 实体，扩展更轻）
   - 验收：跑任意 hermes 命令后，对应 profile 下 `empire-thread.jsonl` 行数 +1，schema 兼容 `Event` 字段（`event_id/ts/profile/event/data/task_id/source`）

2. **渲染并安装 launchd plist** — 写 `core/scripts/install-event-bridge.sh`（参照 `seed-a2a-symlinks.sh`）：
   - 占位符 → 真值：`PYTHON=$(which python3.12)`、`HOME=/Users/alexcai`、`HERMES_HOME=/Users/alexcai/.hermes`、`OBSIDIAN_VAULT=/Users/alexcai/Documents/Obsidian/AlexCai`、`LOG_DIR=/Users/alexcai/.hermes/logs`、`WORKING_DIR=/Users/alexcai/.hermes/plugins/hermes-a2a`
   - `cp ~/Library/LaunchAgents/com.hermes.eventbridge.plist`
   - `HOME=$HOME launchctl bootstrap gui/$(id -u) ...`
   - 验收：`launchctl list | grep com.hermes.eventbridge` 有 PID，`tail -f ~/.hermes/logs/eventbridge.out.log` 看到 1 s 一次心跳

3. **清理陈旧 cursor + 陈旧测试数据**（避免线上误读）
   - 决策一：把 `~/.hermes/profiles/regent/empire-thread.jsonl` 整文件归档/移除，让 daemon 从空文件冷启
   - 决策二：保留并接受 cursor 已指到 EOF（46/46）作为生产 baseline
   - 验收：daemon 启动后 `~/.hermes/event-bridge/cursors/` 内只有正常推进的 cursor，无空跑增量

### P1 — 生产硬化（一周内）

4. **`HINDSIGHT_API_KEY` + `HINDSIGHT_API_URL` 落地**
   - 写入 `~/.hermes/config.yaml`（或 `~/.config/hermes/env`），并在 plist `EnvironmentVariables` 注入
   - 验收：daemon 启后看到 `HindsightSink` 实例化日志，写入一个测试 evt → `pending.jsonl` 出现 1 行 → 下个 flush 投递成功

5. **修正 `HindsightSink._rewrite_pending` 的 IO 放大**（详见 Q1 偏差表第 3 项）
   - 用 `PendingQueue.advance(line_no)` 推进成功项的 cursor，不重写整文件
   - retry/DLQ 项的状态用 sidecar `.meta.jsonl` 记录，让 `pending.jsonl` 保持纯 append-only
   - 验收：在 10 000 行 pending 场景下 flush 一次的 IO 字节数 < 1 MB（当前会重写整 10 MB）

6. **执行 v2 §3.1 验收门禁**
   - 启用 daemon 前后用 `pre_tool_call` 测 100 次延迟 → P99 差 < 5 ms
   - kill -9 daemon × 30 次 / 60 s · enqueue 端零丢失（v2 §3.5）
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
   - pending.jsonl 行数（Hindsight 网络阻塞告警）

9. **跨 profile fan-out 文档化转交** — v2 §四已设计走 `kanban_notify_subs`，但还没有具体的"如何订阅"操作手册纳入 `s6m-config/docs/`。建议补一个 `cross-profile-notify-howto.md`。

10. **向上游 hermes-agent 提 PR 落地 `notification_sources`** — W3 调研发现文档承诺 vs 代码 gap。**非阻塞**，可作为社区贡献（v2 §6.5）。

11. **生产观测** — `pending.jsonl` 行数与 `dlq.jsonl` 行数作为 Grafana 数据点（或 `~/.hermes/event-bridge/metrics.json`），日志接入既有 launchd 日志轮转。

---

## 关键证据索引

| 主张 | 证据命令 / 路径 |
|------|----------------|
| 代码 ↔ 设计一致 | `core/event_bridge/{core,cursor,daemon,paths,pending,dlq}.py` + `sinks/{obsidian,hindsight}.py` |
| 53/53 测试通过 | `python -m pytest tests/unit/test_event_bridge*.py -v` 完整输出，0.19 s |
| MemorySink/SessionSink 已删除 | `ls core/event_bridge/sinks/` 仅 obsidian/hindsight；`grep -rn` 零命中 |
| 代码已同步部署 | `diff -q core/.../core.py ~/.hermes/plugins/.../core.py` 无输出 |
| plist 模板存在 | `core/templates/event-bridge-launchd.plist` |
| plist 未安装 | `ls ~/Library/LaunchAgents/ \| grep -i event` 无输出 |
| daemon 未运行 | `launchctl list \| grep -i 'hermes.event'` 无命中 |
| 16 profile 无 jsonl | `find ~/.hermes/profiles -maxdepth 2 -name empire-thread.jsonl` 仅 regent |
| 上游 hook 无 emit | `grep -rn "empire-thread" ~/.hermes/hermes-agent/` 零命中 |
| Obsidian sink 写过 | `find ~/Documents/Obsidian/AlexCai/88_event-bridge/2026 -name "*.md" \| wc -l` = 46 |
| Hindsight sink 从未跑 | `ls ~/.hermes/event-bridge/hindsight/` 为空 |

---

## 一句话总结

EmpireThread v2 的**代码与测试已 production-ready，链路仍未通电**——P0 三件事（emit hook + 安装 plist + 清陈旧数据）做完即可看到事件从 hermes 命令一路流到 Obsidian，再做 P1 的 Hindsight + 验收门禁，2-3 天可正式上生产。
