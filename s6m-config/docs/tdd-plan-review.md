# TDD 计划审查报告

> **审查对象**：`s6m-config/docs/tdd-test-plan.md` v1.0
> **审查日期**：2026-05-29
> **方法**：现场跑命令验证（hermes CLI、HERMES_HOME 隔离、port-map 正则、jz-skills 部门 SKILL 实际清单）
> **结论摘要**：**TDD 计划基线偏差较多，必须先修订再进入 RED 阶段**。重大偏差 3 项，中等偏差 5 项，可直接落地 2 项。
> **关联**：[[tdd-test-plan]] · [[三省六部×A2A架构方案]] §11 路线图

---

## 0. 审查摘要（TL;DR）

| 等级 | 问题 | 影响 |
|------|------|------|
| 🔴 严重 | **基线"kanban.db 0 字节"已过期** — 实际 2.8MB / 659 tasks / 217 comments | P0-1 表述需重写：从"首次初始化"改为"在隔离 HERMES_HOME 下的首卡生命周期测试" |
| 🔴 严重 | **gateway ≠ kanban dispatcher（部分误读）** — `hermes gateway` 是 messaging gateway，但内嵌 dispatcher tick | P0-1 启动命令对，但语义需澄清：dispatcher 是 gateway 的子组件 |
| 🔴 严重 | **`task_comments` 表 schema 无 `kind` / `in_reply_to` 字段** — 上游 Hermes 自己的表结构无该列 | P0-3 必须先决策：改上游 / metadata 嵌入 / body 前缀约定 |
| 🟡 中等 | hanlinyuan、protocol 两个部门**只有归档 skill** | P0-2 矩阵推荐项需替换 |
| 🟡 中等 | 5 个 profile（default/dispatcher/engineer/planner/reviewer）**无 dept/ 专属目录** | per-task `--skill` 依赖跨部门加载 |
| 🟡 中等 | hermes 版本 v0.14.0 **落后 110 commits** | 某些新能力可能缺失，建议先 `hermes update` |
| 🟡 中等 | `hermes kanban init` **无 `--force` 参数** | EDGE-1（损坏 db 上 force 重建）需改测试设计 |
| 🟡 中等 | `hermes kanban comment` **CLI 无 `--kind` `--in-reply-to`** | P0-3 写入路径需新增工具或 patch |
| 🟢 通过 | `port-map.md` 正则 16/16 匹配 | conftest fixture 设计可直接落地 |
| 🟢 通过 | `HERMES_HOME=/tmp/...` 真实隔离有效 | `tmp_hermes_home` fixture 方案可行 |
| 🟢 通过 | `hermes kanban create --skill <name>` (repeatable) **CLI 原生支持** | P0-2 写入路径无需 patch |
| 🟢 通过 | `hermes kanban dispatch --dry-run --json` 可用 | mock_gateway fixture 可用此命令替代真起 daemon |

---

## 1. jz-skills 实际状态 vs TDD 计划矩阵

### 1.1 12 部门目录现存活 SKILL 实测清单

> 命令：`find ~/code/jz-skills/hermes-3S6M-profiles -name SKILL.md`

| 部门目录 | 活跃 SKILL | 归档 SKILL | 实际计数 |
|----------|-----------|-----------|----------|
| `archivist/` | agent-memory-manager | — | 1 |
| `auditor/` | agent-audit-evaluation | — | 1 |
| `budget/` | agent-cost-manager | — | 1 |
| `common/` | financial-research-agents, three-provinces-constitution | — | 2 |
| `gongbu/` | agent-observability, disk-cleanup, infra-health-check, infra-monitoring, surge-gateway | — | 5 |
| `hanlinyuan/` | **（无）** | deep-research-agent.archived | **0** ⚠️ |
| `jiangzuojian/` | delivery-gate, specialist-engineer | — | 2 |
| `protocol/` | **（无）** | md-to-pdf.archived | **0** ⚠️ |
| `regent/` | 6m-smoke-test, kanban-gate, kanban-orchestrator (v3.5.0), kanban-worker, morning-news-briefing | — | 5 |
| `registry/` | agent-registry | — | 1 |
| `shangshu/` | a2a-protocol | — | 1 |
| `tester/` | agent-security-audit, code-review-toolkit | — | 2 |
| **合计** | **21 活跃** | **2 归档** | — |

### 1.2 三省六部 16 Profile × dept/ 目录覆盖差距

| Profile | A2A 端口 | 期望部门目录 | 实际状态 |
|---------|----------|--------------|----------|
| default (小黄) | 8945 | — | ✅ 文档明确：fallback to `_BASE_TOOLSETS`，无需专属目录 |
| regent (太子) | 8939 | regent/ | ✅ 5 个 SKILL |
| gongbu (工部) | 8898 | gongbu/ | ✅ 5 个 SKILL |
| hanlinyuan (翰林院) | 8702 | hanlinyuan/ | ❌ **0 活跃** —— 仅归档 |
| budget (户部) | 8936 | budget/ | ✅ 1 个 SKILL |
| registry (吏部) | 8928 | registry/ | ✅ 1 个 SKILL |
| protocol (礼部) | 8833 | protocol/ | ❌ **0 活跃** —— 仅归档 |
| archivist (史馆) | 8804 | archivist/ | ✅ 1 个 SKILL |
| auditor (御史中丞) | 8698 | auditor/ | ✅ 1 个 SKILL |
| jiangzuojian (将作监) | 8654 | jiangzuojian/ | ✅ 2 个 SKILL |
| tester (测试) | 8755 | tester/ | ✅ 2 个 SKILL |
| shangshu (尚书) | 8826 | shangshu/ | ✅ 1 个 SKILL |
| **dispatcher (派工)** | 8707 | — | ❌ **无目录** |
| **engineer (兵部)** | 8718 | — | ❌ **无目录** |
| **planner (策划)** | 8728 | — | ❌ **无目录** |
| **reviewer (御史)** | 8761 | — | ❌ **无目录** |

**差距统计**：

- ✅ **11 / 16** profile 有可用 dept skill（含 default fallback）
- ❌ **2 / 16** 部门仅有归档 skill（hanlinyuan, protocol）
- ❌ **4 / 16** profile 无任何 dept 目录（dispatcher, engineer, planner, reviewer）

### 1.3 P0-2 矩阵需要的修订（按表格逐行）

| Profile | TDD 计划原推荐 | 实测差距 | 修订建议 |
|---------|---------------|----------|----------|
| hanlinyuan | `deep-research-agent` | ❌ 已归档 | 改用 `hermes/web-research-router` 或 `hermes/arxiv`（[[三省六部×A2A架构方案]] §8.2 明确"已被 web-research-router 吸收"） |
| protocol | `pdf` | ✅ skill 名对，但属于 `shared/pdf` 而非 `dept/protocol/` | 在矩阵脚注澄清"跨层 per-task 加载" |
| dispatcher | `kanban-orchestrator + agent-registry` | ⚠️ 二者分别在 regent/ 和 registry/ 目录 | 验证 per-task `--skill` 是否能跨 dept 加载（U3 测试覆盖） |
| engineer | `specialist-engineer + delivery-gate` | ⚠️ 二者在 jiangzuojian/ | 同上 |
| planner | `grill-with-docs + kanban-orchestrator` | ✅ grill-with-docs 在 shared/，kanban-orchestrator 在 regent/ | per-task 跨层 |
| reviewer | `code-review-toolkit + agent-audit-evaluation` | ⚠️ 二者在 tester/ 和 auditor/ | per-task 跨部门 |
| regent | `kanban-orchestrator (+kanban-gate +6m-smoke-test)` | ✅ 完全吻合 | 无需修订 |
| tester | `code-review-toolkit + agent-security-audit` | ✅ 完全吻合 | 无需修订 |
| gongbu | `infra-health-check (+infra-monitoring +surge-gateway)` | ✅ 完全吻合 | 无需修订 |

→ **核心结论**：P0-2 矩阵需新增一项"**per-task --skill 跨部门 / 跨层加载验证**"作为核心测试，而不仅仅是"每 profile 至少一个 skill"。这同时变成 M2CL 理论的强证据。

---

## 2. conftest.py Fixture 可行性逐条验证

### 2.1 ✅ `tmp_hermes_home` —— 完全可行

**验证命令**：

```bash
HERMES_HOME=/tmp/test-hermes-isolation hermes kanban init
```

**实测输出**：

```
Kanban DB initialized at /tmp/test-hermes-isolation/kanban.db
Seeded skill(s) into profile default: ...88+ skills...
Discovered 1 profile(s) on disk; any of these can be an --assignee:
  default
```

**结论**：

- `HERMES_HOME` 环境变量被 Hermes CLI 原生识别
- 临时目录被正确初始化为 6 表 sqlite WAL
- **副作用警告**：隔离环境只发现 1 个 profile（default），其他 15 个 profile 看不到。conftest 必须额外做配置 seeding。

**修订 fixture 设计**：

```python
@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    # 关键：复制最小 profile 配置以让 16 profile 被发现
    shutil.copytree(Path.home()/".hermes/profiles", home/"profiles",
                    ignore=shutil.ignore_patterns("*.log", "*.lock"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home
```

### 2.2 ✅ `port_pool` —— 正则 100% 命中

**验证命令**：

```python
PORT_MAP_RE = re.compile(r'^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`')
```

**实测结果**：`Matched: 16 profiles` —— 16/16 全中。

| profile | port | profile | port |
|---------|------|---------|------|
| archivist | 8804 | jiangzuojian | 8654 |
| auditor | 8698 | planner | 8728 |
| budget | 8936 | protocol | 8833 |
| default | 8945 | regent | 8939 |
| dispatcher | 8707 | registry | 8928 |
| engineer | 8718 | reviewer | 8761 |
| gongbu | 8898 | shangshu | 8826 |
| hanlinyuan | 8702 | tester | 8755 |

**结论**：fixture 可直接落地，无需修订。

### 2.3 ⚠️ `mock_gateway` —— 计划设计错误，但有更好替代

**关键发现**：

`hermes gateway start` **不是** kanban dispatcher 的入口，它是 **messaging gateway**（管 Telegram/Discord/WhatsApp 等），但**内嵌**了 dispatcher tick（每 60s 一次）。

`hermes gateway start --help` 输出：

```
options:
  --system    Target the Linux system-level gateway service
  --all       Kill ALL stale gateway processes ...
```

**无 `--dry-run`**。

**但 `hermes kanban dispatch --dry-run --json` 可用**！实测：

```json
{
  "reclaimed": 0,
  "crashed": [],
  "timed_out": [],
  "stale": [],
  "auto_blocked": [],
  "promoted": 0,
  "spawned": [],
  "skipped_unassigned": [],
  "skipped_nonspawnable": []
}
```

→ 这是 dispatcher 决策的**单次 dry-run**，正适合做集成测试。

**修订 fixture 设计**：

| 用途 | 原计划 | 修订 |
|------|--------|------|
| 不真跑 worker，只验决策 | `mock_gateway --dry-run` | 改用 `hermes kanban dispatch --dry-run --json` 并 parse JSON |
| 真起 dispatcher 守护进程 | `hermes gateway start` | 改用 `hermes kanban daemon --pidfile <tmp> --interval 5` 直接起 dispatcher（避免拉起 messaging gateway 的副作用） |

→ **强烈建议**：测试时用 `hermes kanban daemon` 而非 `hermes gateway start`，避免在 CI 中意外向 Telegram / Discord 发消息。

### 2.4 ⚠️ Hermes 版本与 CLI 能力对照表

实测 `hermes --version`：

```
Hermes Agent v0.14.0 (2026.5.16)
Update available: 110 commits behind — run 'hermes update'
```

| TDD 计划假设的 CLI | 实测状态 | 备注 |
|--------------------|----------|------|
| `hermes kanban init` | ✅ 存在，无参数 | EDGE-1 测试设计需调整（无 `--force`） |
| `hermes kanban create --skill X --skill Y` | ✅ 原生支持，`--skill` 可重复 | P0-2 写入路径无需任何 patch！ |
| `hermes kanban create --assignee <profile>` | ✅ 存在 | OK |
| `hermes kanban create --tenant <ns>` | ✅ 存在 | EDGE 测试可用 |
| `hermes kanban comment --kind <DCI>` | ❌ **不存在** | P0-3 必须扩展 CLI 或绕过 |
| `hermes kanban comment --in-reply-to <id>` | ❌ **不存在** | 同上 |
| `hermes kanban complete --summary --metadata` | ✅ 存在 | P0-1 I5 测试可用 |
| `hermes kanban block` / `unblock` | ✅ 存在 | I6 OK |
| `hermes kanban dispatch --dry-run --json` | ✅ 存在 | mock_gateway 替代方案 |
| `hermes kanban daemon --pidfile --interval` | ✅ 存在 | I2 真起 dispatcher |
| `hermes kanban list --status <S>` | ✅ 存在 | E3 OK |
| `hermes kanban show <id>` | ✅ 存在 | OK |
| `hermes kanban runs <id>` | ✅ 存在 | I5 可用 |
| `hermes kanban swarm` | ✅ 存在（新增） | 与 §11 未提及，可纳入未来扩展 |
| `hermes kanban schedule` / `promote` / `reclaim` | ✅ 全部存在 | 边界 case 可用 |
| `hermes kanban diagnostics` (=diag) | ✅ 存在 | doctor 可调用 |

**建议**：

- 进入 RED 阶段前先跑 `hermes update`，否则 110 commit 差距可能引入未知不一致
- 若不便升级，TDD 计划应**锁定 v0.14.0** 并把 `hermes --version` 断言加进 conftest

---

## 3. kanban.db 现状（基线纠正）

### 3.1 实测 schema

```
$ sqlite3 ~/.hermes/kanban.db ".tables"
kanban_notify_subs  task_events  task_runs
task_comments       task_links   tasks
```

✅ 6 表已存在 —— 与 [[三省六部×A2A架构方案]] §7.1 描述一致。

### 3.2 实测数据量

| 表 | 行数 |
|----|------|
| `tasks` | 659 |
| `task_comments` | 217 |
| 数据库大小 | 2.8 MB |
| 最后修改 | 2026-05-28 23:44 |

**结论**：**基线"never initialized / 0 字节"过期**。看板早已被使用（应该是 `kanban-watchdog` cron 或测试运行写入的）。

### 3.3 task_comments 实测 schema

```sql
CREATE TABLE task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_comments_task ON task_comments(task_id, created_at);
```

**仅 5 列，无 `kind`，无 `in_reply_to`**。

→ **这是 P0-3 的根本障碍**：要加 DCI `kind` 字段必须改上游 Hermes（`~/.hermes/hermes-agent/`）。

---

## 4. 关键调整建议

### 4.1 P0-1 调整（5 项）

| # | 原条目 | 调整后 |
|---|--------|--------|
| 1 | "基线 0 字节、never initialized" | "**生产 kanban.db 已 2.8MB / 659 tasks**；测试必须在 `HERMES_HOME=tmp` 隔离环境进行" |
| 2 | I2 测试 `hermes gateway start` | 改用 `hermes kanban daemon --pidfile <tmp> --interval 5`，避免拉起 messaging gateway |
| 3 | U3 `init --force` | **删除**：CLI 不支持。改测"两次 init 不破坏既有行" + "损坏 db 时手动 backup→重建"流程 |
| 4 | E4 doctor check 9 `check_kanban_initialized` | 加判定"在隔离 HERMES_HOME 下也通过"；同时新增 `check_dispatcher_running`（pgrep `hermes kanban daemon`） |
| 5 | fixture 必须复制 profile 配置到隔离 HERMES_HOME | 否则只能看见 default profile，无法测 16 profile 矩阵 |

### 4.2 P0-2 调整（6 项）

| # | 原条目 | 调整后 |
|---|--------|--------|
| 1 | hanlinyuan 必测 `deep-research-agent` | 改用 `hermes/web-research-router`（活跃，已吸收 deep-research） |
| 2 | protocol 必测 `pdf` | 备注：`pdf` 在 `shared/`，**跨层加载** |
| 3 | dispatcher / engineer / planner / reviewer 4 profile 无 dept/ 目录 | 矩阵新增"**跨部门 per-task `--skill`**"标注 |
| 4 | U3 `test_p02_unit_resolve_skills__merges_dept_and_per_task` | **加优先级**：这是 4 个无 dept 目录 profile 的核心验证 |
| 5 | E1 `test_p02_e2e_16profile_matrix` | 矩阵报告必须额外列"**dept skill 来源层**"（dept/<self>、dept/<other>、shared、hermes） |
| 6 | M2CL 多样性度量（DoD 中 cosine ≥ 0.6） | 需先决定 embedding 模型；建议 OpenAI text-embedding-3-small；记录在矩阵报告 |

### 4.3 P0-3 调整（**最重要 —— 路径选择题**）

`task_comments` schema 无 `kind` 字段，且 CLI 无 `--kind`。**有四条路可走**：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. 改 Hermes 上游** | 给 `~/.hermes/hermes-agent/` 加 migration + CLI 扩展，再提 PR 到上游 | 干净，符合 DCI 论文 | 工作量大；可能被上游拒；侵入 110 commits 落后的版本 |
| **B. metadata 嵌入** | comment body 仍是 free text，在 `task_runs.metadata` 或独立表 `task_comment_kinds` 保存 kind | 不动 Hermes core；可作为 hermes-a2a 插件落地 | 多表 JOIN；与 DCI 论文映射间接 |
| **C. body 前缀约定** | 评论 body 以 `[CHALLENGE] ...` 等 14 种前缀开头，由 orchestrator 用 regex 解析 | 0 schema 改动；最快落地 | 易被 LLM 写错；正则脆弱；DCI 论文映射弱 |
| **D. 旁路 SQLite 表** | hermes-a2a 插件在同库新建 `a2a_comment_kinds(comment_id, kind, in_reply_to)` 关联 `task_comments.id` | 与上游解耦；可独立 migration；schema 清晰 | 需 plugin 在 kanban 表外管理生命周期；orchestrator 必须 query 两表 |

→ **建议路径 D**：作为 hermes-a2a 插件落地，独立表 + 与 hermes 主表外键关联。**这必须在 RED 阶段前由父皇拍板**。

### 4.4 conftest 修订

```python
# tests/conftest.py 关键调整

@pytest.fixture(scope="session")
def hermes_version_assert():
    """锁定 hermes 版本，防止 110 commit 差距引入未知不一致"""
    out = subprocess.check_output(["hermes", "--version"], text=True)
    assert "v0.14.0" in out or "v0.15" in out, f"unexpected hermes version: {out}"

@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    src = Path.home()/".hermes"
    # 复制 16 profile 配置（不带数据），让 kanban init 能发现 16 profile
    if (src/"profiles").exists():
        shutil.copytree(src/"profiles", home/"profiles",
                        ignore=shutil.ignore_patterns("*.log", "*.lock", "*.db"))
    # 复制 a2a-registry 与 token 让 A2A 能联动
    shutil.copy(src/".a2a-token", home/".a2a-token")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))  # 进一步隔离 sandbox HOME hack
    yield home

@pytest.fixture
def kanban_db(tmp_hermes_home):
    subprocess.run(["hermes", "kanban", "init"], check=True,
                   env={**os.environ, "HERMES_HOME": str(tmp_hermes_home)})
    return tmp_hermes_home / "kanban.db"

@pytest.fixture
def dispatcher_daemon(tmp_hermes_home, kanban_db):
    """改用 kanban daemon 替代 gateway start，避免拉起 messaging gateway"""
    pidfile = tmp_hermes_home / "dispatcher.pid"
    proc = subprocess.Popen(
        ["hermes", "kanban", "daemon", "--pidfile", str(pidfile),
         "--interval", "5", "--verbose"],
        env={**os.environ, "HERMES_HOME": str(tmp_hermes_home)})
    yield proc
    proc.terminate()
    proc.wait(timeout=10)

@pytest.fixture(scope="session")
def port_pool():
    """16 profile 端口映射，正则已实测 100% 命中"""
    import re
    PORT_MAP_RE = re.compile(r'^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`')
    text = (Path.home()/'code/hermes-a2a/s6m-config/port-map.md').read_text()
    pool = {m.group(1): int(m.group(2))
            for line in text.splitlines()
            for m in [PORT_MAP_RE.match(line)] if m}
    assert len(pool) == 16, f"期望 16 profile，实得 {len(pool)}"
    return pool
```

---

## 5. 修订后的 P0 矩阵（修订草案）

### 5.1 P0-2 16 Profile × Skill 修订矩阵

| Profile | Port | dept/ 目录 | 必测 skill 修订 | 来源层 | 备注 |
|---------|------|-----------|----------------|--------|------|
| default (小黄) | 8945 | — (fallback) | `constitution` + `web-research-router` | shared/, hermes/ | fallback 基线 |
| regent (太子) | 8939 | regent/ | `kanban-orchestrator` | dept/regent | v3.5.0 已就绪 |
| gongbu (工部) | 8898 | gongbu/ | `infra-health-check` | dept/gongbu | 5 dept skill 最丰 |
| hanlinyuan (翰林院) | 8702 | hanlinyuan/ | **`web-research-router`**（修订） | hermes/ | 原 deep-research-agent 已归档 |
| budget (户部) | 8936 | budget/ | `agent-cost-manager` | dept/budget | — |
| registry (吏部) | 8928 | registry/ | `agent-registry` | dept/registry | gateway.py profile |
| protocol (礼部) | 8833 | protocol/ | **`pdf`（跨层）** | shared/ | dept 仅归档 |
| archivist (史馆) | 8804 | archivist/ | `agent-memory-manager` | dept/archivist | — |
| auditor (御史中丞) | 8698 | auditor/ | `agent-audit-evaluation` | dept/auditor | — |
| jiangzuojian (将作监) | 8654 | jiangzuojian/ | `delivery-gate` | dept/jiangzuojian | — |
| tester (测试) | 8755 | tester/ | `code-review-toolkit` | dept/tester | — |
| shangshu (尚书) | 8826 | shangshu/ | `a2a-protocol` | dept/shangshu | — |
| **dispatcher (派工)** | 8707 | — | **`kanban-orchestrator`（跨部门）** | dept/regent | 验证跨 dept |
| **engineer (兵部)** | 8718 | — | **`specialist-engineer`（跨部门）** | dept/jiangzuojian | 验证跨 dept |
| **planner (策划)** | 8728 | — | **`grill-with-docs`（跨层）** | shared/ | 验证跨层 |
| **reviewer (御史)** | 8761 | — | **`code-review-toolkit`（跨部门）** | dept/tester | 验证跨 dept |

→ 标 **修订/跨** 的 6 行是 M2CL 理论的强证据测试。

### 5.2 P0-3 路径决策待办

```
□ 由父皇决定路径 A / B / C / D（建议 D）
□ 若选 D，新增 schema 文件：s6m-config/migrations/001_a2a_comment_kinds.sql
□ 新增 plugin 工具：hermes-a2a/core/comment_kind.py + comment_kind_cli.py
□ orchestrator 路由从两表 JOIN 取数据
□ DoD 增加"上游 hermes 不需任何改动"的回归测试
```

---

## 6. 立刻可执行的行动

| # | 行动 | 命令 / 文件 |
|---|------|-------------|
| 1 | **拍板 P0-3 路径**（A/B/C/D） | 与父皇讨论后回写到本审查文档 §5.2 |
| 2 | **决定是否升级 hermes** | `hermes update`（落后 110 commits） |
| 3 | 把修订后的 conftest 写到 `tests/conftest.py` | §4.4 草案 |
| 4 | 修订 `tdd-test-plan.md` 受影响章节 | P0-1 §1.5、P0-2 §2.3、P0-3 §3.5 |
| 5 | 跑一次 `HERMES_HOME=/tmp/sanity hermes kanban init` 验证 sanity baseline | 立即可做 |
| 6 | 把 hanlinyuan 与 protocol 的归档 skill 从矩阵移除 | 直接改 §2.3 |
| 7 | 给 jz-skills 提 issue：是否要为 dispatcher/engineer/planner/reviewer 补 dept/ 目录 | gh issue |

---

## 7. 风险升级

| 风险 | 原级别 | 修订后 | 理由 |
|------|--------|--------|------|
| Hermes 版本不同步 | 中 | **高** | 实测落后 110 commits，可能新增/删除 CLI 选项 |
| P0-3 schema 改动受阻 | 中 | **高** | 上游 schema 无 kind 列，方案 D 是新引入的复杂度 |
| hanlinyuan/protocol skill 缺失 | 未列 | 中 | 影响矩阵覆盖率，但可用跨层 skill 绕过 |
| 隔离环境只见 1 profile | 未列 | 中 | 必须在 fixture 中显式复制 profile 配置 |
| messaging gateway 副作用 | 未列 | 中 | 测试不能用 `hermes gateway start`，否则可能发真消息 |

---

## 8. 与原 TDD 计划的兼容性

| 章节 | 兼容性 | 操作 |
|------|--------|------|
| §0 通用约定 | ✅ 兼容 | 仅 fixture 实现需替换 |
| §1 P0-1 | ⚠️ 部分 | 删 U3 `--force`；I2 替换命令；E4 调整 doctor check |
| §2 P0-2 | ⚠️ 部分 | 矩阵 4 行修订；U3 测试地位升级 |
| §3 P0-3 | ❌ 阻塞 | 等路径决策；§3.5 实现清单基本重写 |
| §4 跨 P0 共享 | ✅ 兼容 | 正则验证通过 |
| §5 执行序 | ✅ 兼容 | 无需调整 |
| §6 CI 脚手架 | ✅ 兼容 | makefile 目标可直接用 |
| §7 风险 | ⚠️ 升级 | 详 §7 本审查 |

---

## 9. 关联与变更

- 原 TDD 计划：[[tdd-test-plan]] v1.0（待据本审查升 v1.1）
- 架构主文档：[[三省六部×A2A架构方案]] §11 P0 路线图 / §10.2 部门 SKILL 分配
- 端口表：`s6m-config/port-map.md`（解析正则 100% 命中）
- jz-skills 仓库：`~/code/jz-skills/`（53 SKILL.md → 修订实测 23 文件含 2 归档）

### 9.1 变更记录

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-05-29 | v1.0 | 初版：jz-skills 实测对比、fixture 可行性、3 大严重偏差 + 5 中等 + 2 通过 | CC Agent |
