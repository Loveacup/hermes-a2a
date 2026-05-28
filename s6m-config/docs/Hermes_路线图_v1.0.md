# Hermes 路线图 v1.0

> 最后更新：2026-05-28 | 维护者：监国太子 (regent)

## 当前完成度

| 里程碑 | 状态 | 日期 |
|--------|:----:|------|
| v2 底座修复 | ✅ | 2026-05-20 |
| v0.2 A2A 功能 | ✅ | 2026-05-28 |
| 三大支柱骨架 | ✅ | 2026-05-25 |
| 12-Factor 三件套（EmpireThread + context_tags + human_input_tool） | ✅ | 2026-05-26 |
| Dispatcher 中文修复 | ✅ | 2026-05-28 |
| Kanban DB 批量归档缺陷 | 🟡 | 2026-05-28（已知：批量归档导致索引损坏，已恢复） |

---

## 下一阶段方向（5 个，按优先级）

### 🥇 EmpireThread 事件桥 — 打通多记忆系统

**目标**：EmpireThread JSONL 事件流 → 主动 fan-out 到 Hindsight / Obsidian / MEMORY.md / Session DB。

**状态**：设计完成 ✅ | 实施待启动 ⏳

**设计资产**（CC 3 Agent 评估 + 5 加固项设计）：
- 评估报告：`三省六部_Hermes/10_制度/EmpireThread_事件桥_CC评估报告_20260528.md`
- 设计文档：`三省六部_Hermes/20_实施/event-bridge-eval/design-g1~g5.md`
- 工作区：`~/.hermes/workspaces/event-bridge-eval/`

**核心架构**：Event Sourcing + Sink 插件模式
```
事件源 → EmpireThread(JSONL) → EventBridge → [MEMORY, Obsidian, Hindsight, SessionDB]
```

**5 加固项（实施前置条件）**：
- G1: 异步 out-of-band dispatch（launchd sidecar）
- G2: 倒排索引 + cursor 增量消费
- G3: `_source` 白名单 + 重入计数（防自触发死循环）
- G4: MEMORY.md `flock` + 原子 rename
- G5: 队列强制落盘 `pending.jsonl`

**实施估算**：3 周，~550 行 Python（W1 核心+MEMORY → W2 Obsidian → W3 Hindsight+SessionDB）

---

### 🥈 上线专业化 profiles

**目标**：让 reviewer / tester / hanlinyuan 等 profile 真正上线运行，补齐 skills 使 Dispatcher 从"能跑"变"有用"。

**状态**：A2A 服务已上线 ✅ | Skills 验证待做 ⏳

**完成项**：
- 2026-05-28：10 个离线 profile A2A 服务启动（plist Python 3.9→3.11 + launchctl bootstrap），16/16 全绿
- plist 路径提交：`ec55826` fix(plists): system Python 3.9 → venv Python 3.11

**当前问题**：部分 profile Agent Card skills 数偏低（reviewer:2, tester:4, auditor:3），需检查 toolset 配置是否正确暴露全部 skills。

**工作量**：中（plist 配置 + skills 同步 + 模型配置）
**风险**：低

---

### 🥉 v0.3 A2A 协议增强

**目标**：`GET /a2a/tasks` 列表端点、SSE 真流式、fan-out 编排。

**状态**：基本完成 ✅ | fan-out 编排待规划 ⏳

**已实现**（server.py）：
- `GET /a2a/tasks?limit=N&status=X` — 列表端点 (L147-156)
- `GET /a2a/tasks/{id}/stream` — SSE 流式 (L158-161)
- `GET /a2a/tasks/{id}` — 单任务查询 (L163-169)

**待做**：fan-out 编排（应由 planner/dispatcher 负责，非 server.py 层）

**工作量**：低（仅剩 fan-out 编排，其余已实现）
**风险**：低

---

### 4️⃣ 审计全闭环

**目标**：评分写回 task → 低分 → Telegram 告警 → Kanban 复审卡。

**状态**：已完成 ✅

**实现**（`08305ab`）：
- `audit_hook.py` + `server.py`：score_task 后自动调用 `maybe_alert()`
- 阈值 `ALERT_THRESHOLD=0.4`，冷却 5min，去重防骚扰
- 低分 → `hermes send` Telegram 告警 + `kanban create` 复审卡
- best-effort：告警失败不阻塞任务完成

**工作量**：低（~45 行，已完成）
**风险**：低（保守阈值 + 冷却 + 去重）

---

### 5️⃣ 治理流程补正

**目标**：三省流程形同虚设、尚书省空壳问题修复（组织/流程层面）。

**状态**：基本缓解 ✅ | 持续优化 ⏳

**已改善**（2026-05-28 会话）：
- 16/16 A2A 全在线，dispatch 链路可用
- 尚书省 dispatcher + shangshu 双轨运行
- plist 全部改用 venv Python 3.11

**剩余**：kanban-gate 插件目录待恢复、cron coordinator 待重新部署

**工作量**：低（制度和 skill 调整）
**风险**：低

---

## 技术债务

| 项目 | 优先级 | 状态 |
|------|:------:|------|
| CC auth HOME override 陷阱 | P1 | 🟡 已文档化，symlink 修复部分生效 |
| Kanban DB 批量操作并发安全 | P1 | 🟡 已知问题，需 SQLite WAL 模式或分批锁 |
| Scratch workspace GC 导致产出丢失 | P1 | 🟡 已强制持久路径规避 |
| EmpireThread 审计链卡死（12-factor-p1） | P2 | ⚫ 看板重建后已清除 |

---

## 变更记录

| 日期 | 变更 |
|------|------|
| 2026-05-28 | 初始创建。收录 5 方向优先级 + 事件桥 CC 评估 + G1-G5 设计方案 |
