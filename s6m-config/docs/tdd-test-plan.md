# 三省六部 Kanban × A2A — P0 TDD 测试计划

> **版本**：v1.1（基于 [[tdd-plan-review]] 审查修订）
> **目标**：将 §11 路线图的三项 P0 工作以测试驱动方式落地。
> **范围**：P0-1（Kanban 隔离初始化 + Dispatcher daemon + 首卡生命周期）、P0-2（per-task `--skill` 跨 16 profile 差异化 + 跨部门加载）、P0-3（DCI `kind` 字段旁路表 + 讨论线程）
> **方法**：每项 P0 严格遵循 RED → GREEN → REFACTOR；测试 **先于** 实现编写；三层渐进：单元 → 集成 → E2E。
> **当前基线（已修正）**：
> - Hermes v0.15.1 (2026.5.29) — 已升级，最新版本
> - A2A 16/16 healthy v0.2.6
> - `~/.hermes/kanban.db` **2.8MB / 659 tasks / 217 comments**（已被使用，**不是** 0 字节）
> - `task_comments` schema **无 `kind`/`in_reply_to`**，确认走方案 D（旁路表）
> - jz-skills 12 部门目录，**5 个 profile 无 dept/ 目录**（default 用 fallback；dispatcher/engineer/planner/reviewer 走跨部门 per-task）
> - hanlinyuan、protocol 部门 skill 仅有归档版，矩阵已替换为 `web-research-router`、`pdf`
>
> **关联文档**：[[三省六部×A2A架构方案]] §7/§8/§9/§11；[[tdd-plan-review]]；`s6m-config/port-map.md`；`s6m-config/migrations/001_a2a_comment_kinds.sql`；`CLAUDE.md`
>
> 修订时间：2026-05-29

---

## 0. 通用约定

### 0.1 测试目录布局

```
~/code/hermes-a2a/
├── tests/
│   ├── conftest.py                    # 公共 fixture（HERMES_HOME 隔离 + profile seeding）
│   ├── unit/
│   │   ├── test_kanban_init.py        # P0-1 单元
│   │   ├── test_per_task_skill.py     # P0-2 单元
│   │   └── test_comment_kind.py       # P0-3 单元（含 migration 幂等性）
│   ├── integration/
│   │   ├── test_card_lifecycle.py     # P0-1 集成
│   │   ├── test_skill_dispatch.py     # P0-2 集成（含跨部门加载）
│   │   └── test_thread_protocol.py    # P0-3 集成（旁路表 + 视图）
│   └── e2e/
│       ├── test_first_card_e2e.sh     # P0-1 E2E
│       ├── test_16profile_matrix.py   # P0-2 E2E（16 profile × skill）
│       └── test_three_dept_debate.py  # P0-3 E2E（翰林院/工部/太子）
└── s6m-config/migrations/
    └── 001_a2a_comment_kinds.sql      # P0-3 方案 D 主 SQL（已落地）
```

### 0.2 公共 fixture（`tests/conftest.py`）— 修订版

| Fixture | 用途 | 关键实现 |
|---------|------|----------|
| `hermes_version_assert` | session 起锁定 `v0.15.x` | `subprocess.check_output(["hermes","--version"])` |
| `tmp_hermes_home` | 隔离 HERMES_HOME，**复制 16 profile 配置** | `shutil.copytree` 排除 `*.db`/`*.log`/`*.lock` |
| `shared_token` | 写入临时 `.a2a-token` | 复用生产 token，避免与 A2A 双 token 不一致 |
| `port_pool` | 解析 `port-map.md`（**实测 16/16 命中**） | `r'^- \*\*([a-z_]+)\*\*.*端口 \`(\d+)\`'` |
| `kanban_db` | 在 `tmp_hermes_home` 跑 `hermes kanban init` | 返回 sqlite 连接 |
| `a2a_migration_applied` | **新**：把 `001_a2a_comment_kinds.sql` 应用到 `kanban_db` | 验证 3 对象出现 |
| `dispatcher_daemon` | **改名**：用 `hermes kanban daemon --pidfile --interval 5` | 不再用 `hermes gateway start`（避免拉 messaging） |
| `dry_run_dispatcher` | **新**：`hermes kanban dispatch --dry-run --json` 一次 | 解析 JSON 检查决策不真起 worker |

### 0.3 验证执行环境

```bash
# 前置
hermes --version                   # 必须 v0.15.x（v0.15.1 / 2026.5.29 已锁定）

# 单元 / 集成
cd ~/code/hermes-a2a
python -m pytest tests/unit/ -v
python -m pytest tests/integration/ -v --timeout=60

# E2E（需 launchd 16 profile 就位 + venv python 3.11.13）
HERMES_A2A_E2E=1 python -m pytest tests/e2e/ -v --timeout=600
bash tests/e2e/test_first_card_e2e.sh

# 覆盖率门槛（P0 模块 ≥ 85%）
python -m pytest --cov=core --cov-report=term-missing tests/
```

### 0.4 测试命名约定

`test_<P0编号>_<层>_<行为>__<场景>` —— 例：`test_p01_unit_init_db__creates_six_tables`。所有断言失败必须输出 `assert <实际> == <期望>, "<场景说明>"`。

---

## 1. P0-1：Kanban 隔离初始化 + Dispatcher daemon + 首卡生命周期

> **目标**：在隔离 `HERMES_HOME` 下 `hermes kanban init` 创建 6 表 WAL 库；`hermes kanban daemon` 启动 dispatcher（**非** `hermes gateway start`）；从 `kanban create --skill <X>` → `running` → `complete` 全链路打通。

### 1.1 路线图映射

| 路线图项 | 现状（已修正） | 期望状态 |
|----------|---------------|----------|
| §11.1 P0-1 | 生产 `kanban.db` 2.8MB / 659 tasks（**非 0 字节**），无 dispatcher 守护进程 | 隔离环境首卡完整 lifecycle + dispatcher daemon 稳定 tick |

### 1.2 RED：测试先行

#### 1.2.1 单元层（4 个测试，**删除原 U3 --force**）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| U1 | `test_p01_unit_init_db__creates_six_tables` | `hermes kanban init` 在 `HERMES_HOME=tmp` 后 sqlite_master 含 6 表 | `pytest tests/unit/test_kanban_init.py::U1 -v` | 6 表全在 |
| U2 | `test_p01_unit_init_db__wal_mode_enabled` | `PRAGMA journal_mode == 'wal'` | `::U2` | `mode.lower() == "wal"` |
| U3 | `test_p01_unit_init_db__idempotent` | 连续两次 `init` 不抛错、行数不变 | `::U3` | 第二次输出含 "already" 或 0 改动 |
| U4 | `test_p01_unit_tasks_schema__has_required_columns` | `tasks` 表含 `skills`/`model_override`/`current_run_id`/`claim_lock`/`tenant` | `::U4` | 缺一列即 fail；类型 TEXT/TEXT/INTEGER/TEXT/TEXT |

> ⚠️ **原 v1.0 的 U3 `--force` 测试已删除**：CLI 实测无 `--force` 参数；幂等性收编进新 U3。

#### 1.2.2 集成层（6 个测试，I2 改 daemon）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| I1 | `test_p01_integ_lifecycle__triage_to_done` | 单卡走 `triage → todo → ready → running → done` | `pytest tests/integration/test_card_lifecycle.py::I1 -v` | `task_events` 5 条事件；无回退 |
| **I2** | `test_p01_integ_daemon__starts_with_pidfile` | **改用 `hermes kanban daemon --pidfile <tmp> --interval 5`**（不再用 `hermes gateway start`） | `::I2` | pidfile 存在；`pgrep -F <pidfile>` 返回 1 个 pid；进程 cmdline 含 `kanban daemon` |
| I3 | `test_p01_integ_daemon__sigterm_clean_shutdown` | `kill -TERM <pid>` 后 5s 内 daemon 退出 + pidfile 清理 | `::I3` | 退出码 0；pidfile 不存在 |
| I4 | `test_p01_integ_dispatch__claims_ready_task` | 创建 `ready` 卡，daemon 10s 内转 `running` 且 `claim_lock` 非空 | `::I4` | `claim_lock != NULL`；`worker_pid > 0` |
| I5 | `test_p01_integ_complete__writes_summary_metadata` | `hermes kanban complete <id> --summary --metadata` 后 `task_runs.outcome == 'task_achieved'` | `::I5` | summary 字符串相等；metadata JSON 等价 |
| I6 | `test_p01_integ_block__pauses_for_human` | `hermes kanban block` 后状态 `blocked`，daemon 不 claim | `::I6` | 30s 内保持 `blocked`；`task_events` 含 block |

> ⚠️ **重要**：所有集成测试**禁用 `hermes gateway start`**，防止 CI 中向 Telegram/Discord 发真消息。

#### 1.2.3 E2E 层（4 个测试，E4 加 dispatcher check）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| E1 | `test_p01_e2e_first_card__regent_assignee` | 分给 regent (8939)，全链路完成（**真实生产 HERMES_HOME**） | `bash tests/e2e/test_first_card_e2e.sh regent` | 退出 0；终态 `done`；耗时 < 90s |
| E2 | `test_p01_e2e_first_card__gongbu_subprocess` | 分给 gongbu (8898)，走 subprocess（非 API Server） | `bash tests/e2e/test_first_card_e2e.sh gongbu` | `task_runs.metadata.exec_mode == "subprocess"` |
| E3 | `test_p01_e2e_list__cli_visible` | `hermes kanban list --status done` 列出首卡 | `hermes kanban list --status done \| grep <id>` | stdout 含卡 ID 与标题 |
| **E4** | `test_p01_e2e_doctor__kanban_and_daemon_checks` | doctor 新增 check 9 `check_kanban_initialized` **和** check 10 `check_dispatcher_running` 全过 | `bash core/scripts/hermes-a2a-doctor.sh` | 输出含 `Kanban: initialized (6 tables, 659 tasks)` 与 `Dispatcher: daemon pid=NNNN` |

### 1.3 边界与异常 case（已修正）

| 编号 | 场景 | 测试 |
|------|------|------|
| **EDGE-1** | ~~`init --force` 损坏 db~~ → 改：**手动 backup 后重 init 不抛错** | `test_p01_integ_corrupted_db__manual_recovery`：先 `mv kanban.db kanban.db.bak`，再 `init`，验证新 db 干净 |
| EDGE-2 | dispatcher 启动时 8 个 launchd A2A 服务 crashed（obs #596） | `test_p01_integ_daemon__starts_despite_crashed_a2a`：mock kill_stale 路径 |
| EDGE-3 | worker crash（kill -9），任务回 `ready` | `test_p01_integ_crash_recovery__claim_released` |
| EDGE-4 | 同时初始化竞态（两 `init` 并发） | `test_p01_integ_init_race__fcntl_lock`：第二个进程见 `.init.lock` |
| EDGE-5 | `assignee` 不在 16 profile | `test_p01_unit_create_card__rejects_unknown_profile`：CLI 退出码 != 0 |
| EDGE-6 | `summary` > 4KB | `test_p01_unit_complete__truncates_oversize_summary` |

### 1.4 GREEN 阶段最小实现清单

1. `core/scripts/hermes-a2a-doctor.sh`：新增 check 9 `check_kanban_initialized`（sqlite 表数 == 6）+ check 10 `check_dispatcher_running`（pgrep `hermes kanban daemon`）
2. `s6m-config/scripts/start-dispatcher.sh`（**新建**）：包 `HOME=/Users/alexcai HERMES_HOME=... hermes kanban daemon --pidfile /tmp/hermes-dispatcher.pid --interval 60 &`
3. `core/a2a_dispatch.py`：补 `exec_mode` 字段写入 `task_runs.metadata`
4. `tests/conftest.py`：实现 `tmp_hermes_home` + `dispatcher_daemon` fixture（参见 [[tdd-plan-review]] §4.4）

### 1.5 验收 Definition of Done

```
Unit  4/4  pass | Integration 6/6 pass | E2E 4/4 pass
覆盖率 ≥ 85% on core/a2a_dispatch.py + scripts/start-dispatcher.sh
~/.hermes/kanban.db 字节数 > 0 且 PRAGMA integrity_check == ok
hermes kanban daemon 稳定运行 > 10 分钟，零 zombie
doctor.sh 含 "Kanban: initialized" + "Dispatcher: daemon pid=NNNN"
不调用 hermes gateway start —— CI 严禁意外向 Telegram/Discord 发消息
```

---

## 2. P0-2：per-task `--skill` 跨 16 profile 差异化 + 跨部门加载

> **目标**：同 profile 不同卡动态加载不同 skill；16 profile × ≥ 1 专属 skill 矩阵全跑通；**4 个无 dept/ 目录 profile（dispatcher/engineer/planner/reviewer）通过跨部门加载验证 M2CL 反早熟收敛理论**。

### 2.1 路线图映射

| 路线图项 | 现状（已修正） | 期望状态 |
|----------|---------------|----------|
| §11.1 P0-2 | `hermes kanban create --skill` CLI **原生支持**；jz-skills 12 部门目录，活跃 SKILL 21 个（2 归档已剔除） | 16/16 profile 至少 1 张卡带专属 / 跨部门 skill 跑通；M2CL 多样性度量 ≥ 0.6 |

### 2.2 RED：测试先行

#### 2.2.1 单元层（4 个测试）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| U1 | `test_p02_unit_skills_field__json_array_stored` | `hermes kanban create --skill x --skill y` 后 `tasks.skills` 存 JSON 数组 | `pytest tests/unit/test_per_task_skill.py::U1 -v` | `json.loads(row.skills) == ["x","y"]` |
| U2 | `test_p02_unit_skills_field__empty_default` | 不传 skill 时字段为 `[]` 或 NULL | `::U2` | `row.skills in (None, "[]")` |
| **U3 (升级)** | `test_p02_unit_resolve_skills__cross_dept_loading` | resolver 把 `dept/<other_profile>` 下的 skill 也能加载（默认 dept 仅查 self 是 bug）| `::U3` | `resolve("dispatcher", ["kanban-orchestrator"])` 返回 `regent/kanban-orchestrator/SKILL.md` 路径 |
| U4 | `test_p02_unit_resolve_skills__unknown_skill_warns` | 未知 skill 名 warning 不阻断 | `::U4` | `caplog` 含 `unknown skill: foobar` |

> **U3 重要性升级**：4 个无 dept/ 目录 profile 完全依赖此机制。

#### 2.2.2 集成层（6 个测试，**新增 I6 跨部门**）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| I1 | `test_p02_integ_dispatch__per_task_skill_env` | dispatcher spawn worker 时 env 含正确 skill 列表 | `pytest tests/integration/test_skill_dispatch.py::I1 -v` | env 字符串 strictly equal 期望 |
| I2 | `test_p02_integ_worker__skill_actually_triggered` | worker 日志含 `skill loaded: <name>`；`task_runs.metadata.skills_invoked` 非空 | `::I2` | log grep ≥ 1 hit |
| I3 | `test_p02_integ_two_cards_same_profile__different_skills` | tester 两张并发卡，分别带 `code-review-toolkit` 和 `agent-security-audit` | `::I3` | 两份 log 互不污染 |
| I4 | `test_p02_integ_skill_resolution__falls_back_to_dept_only` | 不传 per-task 时仍加载 dept/<profile> 默认 | `::I4` | 默认 skill 出现；无 warning |
| I5 | `test_p02_integ_a2a_taskcontext__skills_passed_through` | A2A `POST /a2a/tasks` 带 `skills` 字段时 `task_handler.py` 透传 | `::I5` | curl + 集成断言一致 |
| **I6 (新)** | `test_p02_integ_cross_dept__dispatcher_loads_regent_skill` | dispatcher (8707) 卡带 `--skill kanban-orchestrator`（属 regent/），跨部门加载成功 | `::I6` | log 含 `loaded from dept/regent/kanban-orchestrator`；M2CL 验证 |

#### 2.2.3 E2E 层（3 + 1 矩阵）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| E1 | `test_p02_e2e_16profile_matrix__one_card_each` | 16 profile 每个一张专属/跨部门 skill 卡，全部 done | `python tests/e2e/test_16profile_matrix.py` | 16/16 完成；< 30min；零 protocol_violation |
| E2 | `test_p02_e2e_regent__orchestrator_creates_subtask` | regent 带 `kanban-orchestrator` 真实创建子卡 + link | `::regent_orchestrator` | `task_links` 新增；≥ 1 子卡 |
| E3 | `test_p02_e2e_tester__security_audit_findings` | tester 带 `agent-security-audit` 输出审查结论 | `::tester_security_audit` | summary 含 "audit"；metadata.findings 非空 |
| **MATRIX** | 16 × ≥ 1 skill 矩阵报告，**含来源层标注** | `tests/reports/p02-matrix-YYYYMMDD.md` | `python tests/e2e/test_16profile_matrix.py --report` | 含 profile / skill / **source_layer**（dept-self/dept-other/shared/hermes/fallback）/ 耗时 / 状态 |

### 2.3 16 Profile × Skill 修订矩阵（**v1.1 已修订 6 行**）

| Profile | Port | dept/ 目录 | 必测 skill | 来源层 | 修订标记 |
|---------|------|-----------|------------|--------|----------|
| default (小黄) | 8945 | — fallback | `three-provinces-constitution` + `web-research-router` | shared / hermes | — |
| regent (太子) | 8939 | regent/ | `kanban-orchestrator` (v3.5.0) | dept-self | — |
| gongbu (工部) | 8898 | gongbu/ | `infra-health-check` | dept-self | — |
| **hanlinyuan (翰林院)** | 8702 | hanlinyuan/（仅归档） | **`web-research-router`** | **hermes (跨层)** | 🔁 替换 `deep-research-agent.archived` |
| budget (户部) | 8936 | budget/ | `agent-cost-manager` | dept-self | — |
| registry (吏部) | 8928 | registry/ | `agent-registry` | dept-self | — |
| **protocol (礼部)** | 8833 | protocol/（仅归档） | **`pdf`** | **shared (跨层)** | 🔁 替换 `md-to-pdf.archived` |
| archivist (史馆) | 8804 | archivist/ | `agent-memory-manager` | dept-self | — |
| auditor (御史中丞) | 8698 | auditor/ | `agent-audit-evaluation` | dept-self | — |
| jiangzuojian (将作监) | 8654 | jiangzuojian/ | `delivery-gate` | dept-self | — |
| tester (测试) | 8755 | tester/ | `code-review-toolkit` | dept-self | — |
| shangshu (尚书) | 8826 | shangshu/ | `a2a-protocol` | dept-self | — |
| **dispatcher (派工)** | 8707 | — 无目录 | **`kanban-orchestrator`** | **dept-other (regent)** | 🆕 跨部门 / M2CL |
| **engineer (兵部)** | 8718 | — 无目录 | **`specialist-engineer`** | **dept-other (jiangzuojian)** | 🆕 跨部门 / M2CL |
| **planner (策划)** | 8728 | — 无目录 | **`grill-with-docs`** | **shared (跨层)** | 🆕 跨层 / M2CL |
| **reviewer (御史)** | 8761 | — 无目录 | **`code-review-toolkit`** | **dept-other (tester)** | 🆕 跨部门 / M2CL |

→ 标 🔁/🆕 共 6 行是 v1.1 修订重点；后 4 行是 M2CL 强证据测试。

### 2.4 边界与异常 case

| 编号 | 场景 | 测试 |
|------|------|------|
| EDGE-1 | skill 名拼错（如 `kanban-orchestratr`） | U4 |
| EDGE-2 | skills 列表 > 10 个性能压测 | `test_p02_integ_skills__many_loading_perf`：worker 启动 < 15s |
| EDGE-3 | 同名 skill dept + per-task 双声明 | U3：dedup 不抛错 |
| EDGE-4 | SKILL.md 语法错误 | `test_p02_integ_skill_loader__bad_yaml_isolates`：仅该 skill skip |
| EDGE-5 | `pi/` 5 个 Windows-only skill | E2E 矩阵自动 skip，报告标 `skipped: platform=windows` |
| EDGE-6 | profile + skill ACL（如 reviewer 不许 skill `kanban-orchestrator`） | `test_p02_integ_skill_acl__profile_restricted` |
| **EDGE-7 (新)** | 跨部门加载时 dept skill 与 per-task 同名（regent 自带 + dispatcher 借用） | `test_p02_integ_cross_dept__no_collision` |

### 2.5 GREEN 阶段最小实现清单

1. `core/skill_resolver.py`（**新建** ~100 行）：扫描 `~/code/jz-skills/{shared,hermes,hermes-3S6M-profiles/*}`，按 `(profile, skill_name)` 解析路径，支持 dept-self → dept-other → shared → hermes 四级 fallback
2. `core/task_handler.py`：注入 worker env `HERMES_TASK_SKILLS=<csv>` + `HERMES_SKILL_SOURCE_LAYERS=<csv>`
3. `core/a2a_dispatch.py`：透传 `skills` 字段从 `POST /a2a/tasks` 到 `kanban_create`
4. `s6m-config/docs/per-task-skill-matrix.md`（**新建**）：本节 §2.3 表格 + E2E 矩阵报告对照
5. **不改 Hermes 上游**：`hermes kanban create --skill` 已原生支持

### 2.6 验收 Definition of Done

```
Unit 4/4 pass | Integration 6/6 pass | E2E 3/3 + Matrix 16/16 pass
M2CL 反早熟验证：同 profile 不同 skill，输出 cosine ≤ 0.4（embedding: text-embedding-3-small）
4 个无 dept/ 目录 profile 全部通过跨部门 / 跨层加载（dispatcher/engineer/planner/reviewer）
矩阵报告 tests/reports/p02-matrix-*.md 含 source_layer 列且提交到 s6m-config/docs/
零跨 profile 污染（worker log 仅含声明的 skill）
```

---

## 3. P0-3：DCI `kind` 字段旁路表 + 讨论线程（**方案 D**）

> **决策**：不动上游 Hermes，由 hermes-a2a 插件在 `~/.hermes/kanban.db` 中**新建旁路表 `a2a_comment_kinds`**，通过 `comment_id` 软外键关联 `task_comments.id`；orchestrator 路由从联合视图 `a2a_thread_view` 取数据。
>
> **migration 主文件**：`s6m-config/migrations/001_a2a_comment_kinds.sql`（已落地）
> **回滚文件**：`s6m-config/migrations/001_a2a_comment_kinds_rollback.sql`（GREEN 阶段补）

### 3.1 路线图映射

| 路线图项 | 现状 | 期望状态 |
|----------|------|----------|
| §11.1 P0-3 | `task_comments` 无 kind 列；`hermes kanban comment` 无 `--kind` 参数 | 旁路表 + 视图 + CLI wrapper + orchestrator 路由命中 ≥ 90% |

### 3.2 DCI 14 种 kind 枚举（CHECK 约束已在 SQL 落地）

```python
class CommentKind(str, Enum):
    PROPOSE          = "propose"
    ASK              = "ask"
    EVIDENCE_FOR     = "evidence_for"
    EVIDENCE_AGAINST = "evidence_against"
    CHALLENGE        = "challenge"
    CLARIFY          = "clarify"
    REFINE           = "refine"
    CONCEDE          = "concede"
    SYNTHESIZE       = "synthesize"
    SUMMARIZE        = "summarize"
    META_DIRECTIVE   = "meta_directive"
    VOTE_FOR         = "vote_for"
    VOTE_AGAINST     = "vote_against"
    ABSTAIN          = "abstain"
```

### 3.3 RED：测试先行

#### 3.3.1 单元层（6 个测试，**新增 U6 视图**）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| U1 | `test_p03_unit_kind_enum__14_values` | `CommentKind` 14 项 | `pytest tests/unit/test_comment_kind.py::U1 -v` | `len(CommentKind) == 14` |
| U2 | `test_p03_unit_migration__creates_three_objects` | 跑 `001_a2a_comment_kinds.sql` 后存在 `a2a_comment_kinds` 表 / `a2a_thread_view` 视图 / `a2a_schema_versions` 表 | `::U2` | 3 个 sqlite_master 行 |
| U3 | `test_p03_unit_migration__idempotent` | 重复执行 SQL 不报错且 schema_versions 不重复 v=1 | `::U3` | 第二次 INSERT OR IGNORE；行数不变 |
| U4 | `test_p03_unit_insert__rejects_invalid_kind` | 写入 `kind='nonsense'` 触发 CHECK 约束 | `::U4` | sqlite3.IntegrityError |
| U5 | `test_p03_unit_insert__rejects_self_reply` | `in_reply_to == comment_id` 触发 CHECK | `::U5` | sqlite3.IntegrityError |
| **U6 (新)** | `test_p03_unit_view__legacy_comment_defaults_to_propose` | `a2a_thread_view` 对无旁路记录的 217 条历史 comment 返回 `kind='propose'`、`has_a2a_record=0` | `::U6` | 视图查询命中 217 行 |

#### 3.3.2 集成层（7 个测试，**新增 I7 软外键**）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| I1 | `test_p03_integ_thread__ordered_by_created_at` | `a2a_thread_view` 按 `(task_id, created_at)` 排序 | `pytest tests/integration/test_thread_protocol.py::I1 -v` | mono-increasing |
| I2 | `test_p03_integ_orchestrator__routes_by_kind` | `ASK` → 子卡指派 hanlinyuan；`CHALLENGE` → regent | `::I2` | `task_links` 新增；assignee 正确 |
| I3 | `test_p03_integ_vote__majority_triggers_synthesize` | 3 `VOTE_FOR` + 1 `VOTE_AGAINST` 触发自动 SYNTHESIZE | `::I3` | 自动 comment.kind == synthesize；卡进 review |
| I4 | `test_p03_integ_deadlock_guard__forces_synthesize` | 连续 5 轮无新论据 → META_DIRECTIVE + SYNTHESIZE | `::I4` | 第 5 轮注入 |
| I5 | `test_p03_integ_meta_directive__only_regent_or_dispatcher` | 非 regent/dispatcher 写 `meta_directive` 被拒 | `::I5` | 应用层 403（不是 sqlite 约束） |
| I6 | `test_p03_integ_evidence_metadata__source_url_required` | `evidence_for/against` 必须含 `metadata.source_url` 或 `metadata.task_ref` | `::I6` | 应用层验证拒写 |
| **I7 (新)** | `test_p03_integ_soft_fk__rejects_orphan_comment_id` | 写入 `comment_id` 不存在于 `task_comments` 时应用层拒绝 | `::I7` | comment_kind.py 抛 `ValueError("comment_id not found")` |

#### 3.3.3 E2E 层（3 个测试）

| # | 测试名 | 验证内容 | 运行命令 | 预期结果 |
|---|--------|----------|----------|----------|
| E1 | `test_p03_e2e_three_dept_debate__a2a_should_we_replace` | 翰林院 (8702) + 工部 (8898) + 太子 (8939) 真实辩论"是否用 hermes-a2a-preview 替代当前 A2A" | `python tests/e2e/test_three_dept_debate.py --topic="replace-a2a"` | thread ≥ 6 comments；含 PROPOSE/EVIDENCE/CHALLENGE/SYNTHESIZE 4 种 kind；卡 done |
| E2 | `test_p03_e2e_debate_vs_vote__matches_paper` | 对照 [[#§8.4 Debate or Vote]]：纯辩论无明显增益，引入 regent 仲裁后准确率提升 ≥ 10% | `::e2e_debate_vs_vote` | 对照报告 `tests/reports/p03-dci-YYYYMMDD.md` |
| E3 | `test_p03_e2e_dashboard__thread_visible_with_kind_chip` | Hermes Dashboard 渲染 kind 标签彩色 chip | `bash tests/e2e/test_dashboard_thread.sh` | HTML 含 `data-kind="challenge"` |

### 3.4 边界与异常 case

| 编号 | 场景 | 测试 |
|------|------|------|
| EDGE-1 | thread > 30 条 CLI 截断 | `test_p03_integ_thread__pagination` |
| EDGE-2 | body > 4KB | `test_p03_unit_comment__body_size_limit`：硬截断 + warning |
| EDGE-3 | 同 agent 1s 内 10 条评论 | `test_p03_integ_rate_limit__per_agent`：≥ 5 条触发 throttle |
| EDGE-4 | 14 kind 外希望扩展（如 `RETRACT`） | `test_p03_unit_kind_enum__no_silent_extension` + ADR |
| EDGE-5 | 跨 board 引用违反 §7.7 | `test_p03_integ_in_reply_to__rejects_cross_board` |
| EDGE-6 | `ABSTAIN` 不计入多数 | I3 扩展 |
| **EDGE-7 (新)** | 上游 Hermes 改 `task_comments.id` 类型（INTEGER → UUID） | `test_p03_unit_migration__detects_upstream_drift`：启动时校验 PK 类型 |

### 3.5 GREEN 阶段最小实现清单（**方案 D 全清单**）

1. **`s6m-config/migrations/001_a2a_comment_kinds.sql`** ✅ 已落地（含主表 + 视图 + 元表，已在 217 条真实 comment 上验证 LEFT JOIN）
2. `s6m-config/migrations/001_a2a_comment_kinds_rollback.sql`（**新建**）：`DROP VIEW a2a_thread_view; DROP TABLE a2a_comment_kinds;` 但保留元表行
3. `core/comment_kind.py`（**新建** ~120 行）：
   - `CommentKind` Enum
   - `apply_migration(db_path)` 幂等执行 SQL
   - `record_kind(comment_id, kind, in_reply_to=None, metadata=None)` 含软外键校验
   - `get_thread(task_id)` 查 `a2a_thread_view`
4. `core/comment_kind_cli.py`（**新建** ~60 行）：包 `hermes kanban comment` + 后置 `record_kind` 调用
   - 用法：`python -m core.comment_kind_cli <task_id> "body" --kind challenge --in-reply-to 42`
5. `core/orchestrator_router.py`（**新建** ~150 行）：
   - 监听 `a2a_thread_view` 新行（轮询 1s）
   - 路由表：`ASK→hanlinyuan`、`CHALLENGE→regent`、`EVIDENCE_*→logger`、`VOTE_*→aggregator`
   - 自动收敛：5 轮无新论据 → 注入 META_DIRECTIVE + SYNTHESIZE
6. `core/discuss.py`：ROLEPLAY 模式每轮发言额外调 `comment_kind_cli`，回写到旁路表
7. `s6m-config/docs/dci-protocol.md`（**新建**）：14 kind 语义 + 路由策略 + EDGE-7 上游漂移检测策略
8. `core/scripts/hermes-a2a-doctor.sh`：新增 check 11 `check_a2a_comment_kinds_schema`

### 3.6 验收 Definition of Done

```
Unit 6/6 pass | Integration 7/7 pass | E2E 3/3 pass
~/.hermes/kanban.db 含 a2a_comment_kinds 表 + a2a_thread_view 视图
DCI 14 kind 全部至少在一个测试中出现
E1 三省辩论 demo 连续 3 次稳定 done（复现率 100%）
orchestrator 路由命中 ≥ 90%（100 条合成 comment 样本）
对照实验（纯辩论 vs 辩论+regent 仲裁）准确率提升 ≥ 10%
上游 Hermes 零改动 —— 回滚 SQL 可干净移除全部 A2A 表
```

---

## 4. 跨 P0 共享：A2A 端口 ↔ Profile 映射

所有集成 / E2E 测试通过解析 `s6m-config/port-map.md` 获取端口，禁硬编码。

```python
# tests/conftest.py（已实测 16/16 命中）
import re
PORT_MAP_RE = re.compile(r"^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`")

def load_port_map():
    path = Path("~/code/hermes-a2a/s6m-config/port-map.md").expanduser()
    return {m.group(1): int(m.group(2))
            for line in path.read_text().splitlines()
            for m in [PORT_MAP_RE.match(line)] if m}

@pytest.fixture(scope="session")
def port_pool():
    pool = load_port_map()
    assert len(pool) == 16, f"期望 16 profile，实得 {len(pool)}"
    return pool
```

端口校验：

| 校验 | 测试 |
|------|------|
| 端口公式 `8650 + sha256(profile) % 300` 与 port-map.md 一致 | `tests/unit/test_port_map__formula_consistent` |
| 16 profile 全部出现在 launchctl bootstrap 列表 | `tests/integration/test_launchd__16_plists` |
| A2A 8654–8945 与 API Server 8642–8643 不交叉 | `tests/unit/test_port_map__no_overlap_with_api_server` |

---

## 5. 三 P0 执行序与依赖

```
P0-1 (Kanban init + daemon 隔离环境)
    │ → task_comments 表已存在（实测 2.8MB / 217 comments）
    ▼
P0-3 (旁路表 + DCI kind)        P0-2 (per-task --skill + 跨部门)
    │  方案 D，独立 schema             │
    └─────────┬───────────────────────────┘
              ▼
        综合 E2E：三省辩论 + 差异化 skill 同卡
        (tests/e2e/test_full_integration.py)
```

| 阶段 | 启动条件 | 完成判据 |
|------|----------|----------|
| 阶段 1：P0-1 | 隔离 fixture 就绪 | DoD §1.5 |
| 阶段 2：P0-2 + P0-3 并行 | P0-1 DoD 通过 | DoD §2.6 + §3.6 |
| 阶段 3：综合 E2E | 二者都过 | `test_full_integration` 全绿 |

---

## 6. CI / 本地脚手架

```bash
make tdd-p01           # pytest tests/{unit,integration,e2e} -k p01
make tdd-p02
make tdd-p03
make tdd-all-red       # RED 阶段：测试应全红
make tdd-all-green     # GREEN 阶段：实现后全绿
make coverage-report

# P0-3 专用
make apply-migration   # sqlite3 ~/.hermes/kanban.db < migrations/001_...sql
make rollback-migration
```

`git push` pre-push hook 触发 `make tdd-all-green`；失败结果写入 `s6m-config/docs/audits/tdd-failures-YYYYMMDD.md`。

---

## 7. 风险与备选方案（v1.1 已升级）

| 风险 | 级别 | 缓解 |
|------|------|------|
| ~~Hermes 版本不同步~~ | 已消除 | 已升级 v0.15.1（2026-05-29） |
| Hermes 升级后 CLI 选项变化 | 低 | conftest `hermes_version_assert` 锁 v0.15.x |
| 上游 `task_comments.id` 类型变更 | 中 | EDGE-7 启动时校验 + dci-protocol.md 应急方案 |
| per-task skill 跨部门加载未实现 | 中 | I6 + U3 单独测试；如失败回退到全量注入 |
| DCI 14 kind 过细 agent 频繁出错 | 中 | 第一阶段先实现 6 核心 kind（PROPOSE/ASK/CHALLENGE/EVIDENCE_FOR/SYNTHESIZE/CONCEDE）；其余在 ADR 渐进 |
| hermes-a2a-preview 提前成熟 | 低 | §6.5 已注明：P1-1 评估在 P0 完成后才决策 |
| 测试意外向 Telegram/Discord 发消息 | **中** | **CI 严禁 `hermes gateway start`**；只用 `hermes kanban daemon` |
| 隔离环境只发现 1 profile | 已缓解 | fixture 显式 `shutil.copytree` profiles 配置 |

---

## 8. 与现有文档关联

- 主架构：[[三省六部×A2A架构方案]] §7/§8/§9/§11
- 审查报告：[[tdd-plan-review]]（本计划 v1.1 即据此修订）
- 项目约束：`~/code/hermes-a2a/CLAUDE.md` §"关键约束"
- 端口映射：`s6m-config/port-map.md`
- 方案 D 主 SQL：`s6m-config/migrations/001_a2a_comment_kinds.sql`
- DCI 原始论文：arXiv 2603.11781（[[三省六部×A2A架构方案#§8.1 DCI]]）
- M2CL 论文：arXiv 2602.02350（[[三省六部×A2A架构方案#§8.3 M2CL]]）
- Debate or Vote：arXiv 2508.17536（[[三省六部×A2A架构方案#§8.4 Debate or Vote]]）

---

## 9. 变更记录

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-05-29 | v1.0 | 初版：三 P0 TDD 测试计划，单元 14 + 集成 17 + E2E 10 + 矩阵 16 共 57 个测试 | CC Agent |
| 2026-05-29 | **v1.1** | **基于 [[tdd-plan-review]] 修订**：P0-1 删 U3 `--force`、I2 改 `kanban daemon`、E4 加 dispatcher check；P0-2 矩阵 6 行修订 + 新增 I6 跨部门加载 + EDGE-7；P0-3 全部按方案 D 重写（migration 已落地）+ 新增 U6 视图 + I7 软外键测试；Hermes 升级到 v0.15.1；总测试数：**单元 14 + 集成 19 + E2E 10 + 矩阵 16 = 59 个** | CC Agent |
