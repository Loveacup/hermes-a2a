# 三省六部 A2A 体系优化方案

## 摘要
- 全量 16-profile A2A 部署完成（PORT_RANGE=300 公式，零碰撞，全部 launchd 监管）
- 本文档综合 3 个 Explore agent 的设计成果，从「能力发现」「自动审计」「文档归档」三个支柱上规划 A2A 体系化使用
- 完整原始设计文档：`/tmp/s6m-dispatcher-design.md`、`/tmp/s6m-audit-design.md`、`/tmp/s6m-docs-design.md`

## 三大支柱总览

### 支柱一：能力发现 + 智能调度（尚书省 dispatcher）
- **目标**：让 shangshu dispatcher 通过 A2A Agent Card 实时知道每个部门会干什么，把任务派给最合适的部门——而不仅靠 Kanban 关键词匹配
- **核心机制**：discovery 缓存 + skill-coverage 评分 + A2A-first hybrid 回退到 Kanban
- **新文件**：`~/.hermes/profiles/shangshu/a2a_dispatch.py`（~80 行）
- **修改**：`dispatch.py` 加 `--a2a` 启用标志位

### 支柱二：自动审计闭环（御史台 reviewer）
- **目标**：A2A task 完成 → 自动审计执行质量 → 反馈
- **核心机制**：webhook 模式——server.py 的 `_execute_task()` 在 handle_task() 返回后调用 `audit_hook.trigger_audit()`
- **新文件**：`~/.hermes/plugins/hermes-a2a/audit_hook.py`（~140 行）
- **修改**：server.py 在 _execute_task 里加 3 行：`from audit_hook import trigger_audit` + `trigger_audit(_tasks[tid])`
- **审计输出**：4 维度评分（执行/准确/合规/重试），低分自动告警，违规触发 Kanban 卡

### 支柱三：知识库自动归档（翰林院 hanlinyuan）
- **目标**：定时拉 16 profile 的 Agent Card + 任务历史 → 生成 Obsidian 文档
- **核心机制**：launchd timer 周一 00:00（周报）+ 每日 20:00（增量任务史）
- **新文件**：`~/.hermes/plugins/hermes-a2a/docs_generator.py`（~60 行）
- **生成文档**：`三省六部能力图谱.md` / `最近任务史/2026-Wxx.md` / `各部Agent卡片/<profile>.md` × 16

## 共享前提（三个支柱都依赖）

- **统一端口公式**：`8650 + sha256(profile) % 300`，零碰撞，所有 16 profile 部署确认
- **统一 Agent Card schema**：A2A 1.0 spec（id/name/description/examples/tags 全字段）
- **统一 task 状态机**：working → completed/failed，artifact 含 mode/response/duration_s
- **统一进程监管**：launchd KeepAlive=true + ThrottleInterval=30，崩溃 ~1s 内复活

## 关键路径示意（端到端任务流）

```
[Kanban: 用户创建 todo 任务]
    ↓
[shangshu dispatcher]
    ├─→ 加载 capability-map.yaml
    ├─→ discover_agent_cards() 拉 15 个 sibling 的 /a2a/.well-known/agent-card.json（300s TTL 缓存）
    ├─→ match_to_profiles(task_capabilities, cards) 得分排序
    ├─→ POST /a2a/tasks 到 best-match profile（如 engineer:8718）
    └─→ Kanban 评论："Routed via A2A to engineer, score 92%"
            ↓
        [engineer A2A server]
            ├─→ POST 立即返 status=working + task id
            ├─→ Thread → handle_task() → subprocess hermes chat (mode=subprocess)
            └─→ task["status"] = "completed", artifact 写入 response
                    ↓
                [audit_hook.trigger_audit(task)] ← 自动触发
                    ├─→ POST /a2a/tasks 到 reviewer:8761（让御史台审）
                    ├─→ 审计结果 audit_result 回写 _tasks[tid]
                    └─→ 评分 < 60 → Telegram 告警 + Kanban 卡建议复审
                            ↓
                        [hanlinyuan docs_generator.py（异步定时）]
                            ├─→ launchd timer 每日 20:00 跑
                            ├─→ 拉 16 endpoints task history 增量
                            └─→ 写 Obsidian/最近任务史/2026-Wxx.md
```

## 实施优先级 (按风险/价值排序)

### v0.1 MVP（高价值低风险，立刻可做）
- 支柱三 docs_generator.py 的「能力图谱 + agent 卡片」静态生成（agent #3 v0.1 范围）
- 支柱一 discovery + skill-matching 只读路径（不真派工，仅落 Kanban 评论显示「应该派给 X」），用于人工验证算法正确性
- 支柱二 audit_hook.py 的 score-only 模式（评分写回 task，不告警、不重试）

### v0.2（3-5 天内，需端到端验证）
- 支柱一 A2A-first hybrid 真派工（A2A POST → 等任务完成 → 失败回退 Kanban）
- 支柱二 audit + Telegram 告警 + Kanban 复审卡
- 支柱三 任务史增量拉取 + 变更检测（hash diff 不重写 Obsidian）

### v0.3+（需要更多基础设施）
- A2A spec 缺的 `GET /a2a/tasks` 列表端点（agent #2 / #3 都依赖；现在没有，得先在 server.py 加）
- 跨部门 fan-out / dependency 编排（应该由 planner 而非 dispatcher 负责）
- 长任务异步 SSE 真流式（替换现在的 fire-and-forget）

### 明确不做（agent 们已识别为越界）
- 实时 push 通知 → 那是 EmpireThread 的事
- 跨部门成本核算 → 御史台/auditor 的事
- Obsidian 自动链接 → 用户手工管
- A2A 端点作为「广播总线」 → A2A 本质是 RPC，需要 pub/sub 应该单独搞

## 各支柱的关键风险（agent 自识别）

### dispatcher 支柱的风险
- ⚠️ 多部门 fan-out + 依赖关系：当前算法只挑「最佳一个」，多步任务还得人工拆解
- ⚠️ A2A 后台轮询：task_id 落 Kanban 后谁去查完成？需要新增 background daemon
- ⚠️ capability versioning：profile 升级了 SKILL_MAP 后旧 Kanban 卡的 required_capabilities 可能不再匹配

### audit 支柱的风险
- ⚠️ 审计循环：reviewer 自己也是 A2A 端点；reviewer 的 task 完成又触发 audit_hook 会无限递归——必须在 audit_hook 里加 self-skip
- ⚠️ 御史台被打挂：所有 task 完成都 POST 给 reviewer，单点瓶颈；建议加 rate limit + queue
- ⚠️ 隐私：task 原始 prompt 可能含 API key / 用户邮箱；audit_hook 必须 redact 后再前转（agent 已经设计了 `_redact_secrets()`）

### docs 支柱的风险
- ⚠️ Obsidian sync churn：每次 docs_generator 跑都重写文件 → Obsidian iCloud 来回同步；必须做 hash diff
- ⚠️ A2A `/a2a/tasks` 列表端点不存在：agent #3 已经指出现在拉不到完整任务史，要先在 server.py 加 list endpoint，或用 Supermemory 长期记忆后端代替（ADR-005 已确立 Supermemory 为唯一长期记忆后端）
- ⚠️ 16 endpoints 同时 timeout：docs_generator 单线程顺序拉，最坏 16×3s=48s；可以接受，但极端情况建议加 ThreadPoolExecutor

## 三个 agent 的产出归档与判读

### agent #1 (dispatcher) — 高质量、直接可实施
- 完整的 discovery + matching + hybrid 算法，pseudo-code 包含错误处理
- 评分公式（keyword 0.6 + skill_coverage 0.4）合理但需调参
- 建议先做 read-only 显示，跑 1 周看决策是否对

### agent #2 (audit) — 创新但需谨慎
- webhook 模式选得对（替代轮询），但漏掉了「reviewer 审 reviewer」递归风险，需要主 agent 在实施时加 guard
- 4 维度评分模型（执行/准确/合规/重试）需要先离线打分 50 个历史 task 才能定阈值；不要先开自动告警
- _redact_secrets() 正则需要 case-by-case 测，目前只覆盖 API key 不覆盖 prompt 里嵌入的 password 字符串

### agent #3 (docs) — 最实用但范围最窄
- 拉 Agent Card 生成能力图谱是 1 天内能交付的；任务史依赖目前没有的 `/a2a/tasks` 列表端点
- 状态文件 `~/.hermes/.docs-gen-state.json` 做 hash diff 是好主意
- v0.1 MVP（6 profile 能力图谱）建议作为 Phase 1 的「彩蛋」立刻交付

## 主 agent 决议

- **马上做**：agent #3 v0.1 docs_generator（能力图谱 + 16 个 agent 卡片，纯生成，不影响运行系统）
- **本周做**：agent #1 read-only dispatch（只显示「应该派给谁」，不真派）
- **下周做**：agent #2 audit_hook MVP（评分写回 task，不告警不重试，跑 50 个 task 打分校准）
- **延期**：A2A `/a2a/tasks` 列表端点（涉及 server.py 改 schema，跟下一个版本一起做）

## 部署证据（写入本 doc 的前提）

- 16/16 A2A endpoints healthy（PORT_RANGE=300 公式端口）
- 2/2 API Server (default:8642, regent:8643) healthy
- E2E chain test: dispatcher → engineer A2A task `e2e-disp-to-engineer-001` 9.79s 内 status=completed，artifact.mode=subprocess，response="ENGINEER_RECEIVED_DISPATCH"
- 见 `/tmp/a2a-full-deploy.log` 完整部署 trace

## 关联文档
- 上轮再审计：`/tmp/hermes-a2a-reaudit-report.md`
- 架构对比：`docs/architecture-comparison.md`
- 方法论 + ADR：`docs/methodology.md`
- 跟踪：`docs/tracking.md`
- 原始 agent 设计：`/tmp/s6m-dispatcher-design.md`、`/tmp/s6m-audit-design.md`、`/tmp/s6m-docs-design.md`
