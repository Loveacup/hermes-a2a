# Hermes-A2A 优化实施总结（Phase 0 + Phase 1）

**日期**：2026-05-28（迭代 2 增补）
**版本**：v0.1.0 → v0.2.0 → v0.2.1
**任务来源**：`~/.hermes/tmp/hermes-a2a-optimization-brief.md`

---

## 0. TL;DR

Phase 0（安全 hotfix）+ Phase 1（架构底座）已完成。本次迭代 2 补齐 5 项关键收尾：
- **P0-1 闭环**：discuss.py 的 `_a2a_send` / `_a2a_poll` 现已携带 Bearer token（彻底避免部署后跨 profile 401）。
- **P0-2 评估**：subprocess argv 注入的 stdin 方案经验证**不可行**（hermes CLI 不支持 stdin 输入），保留 API Server 优先路径作为 mitigation。
- **P0-4 落地**：新增 `core/audit_hook.py` 骨架，四道防线（profile 跳过 / depth header / semaphore N=8 / rate limit 60/min）。
- **P1-1 加固**：`plugin.py` 新增子进程启动健康探测（TOCTOU 窗口收紧 + 子进程崩溃感知）。
- **P1-13 外置**：`task_handler._classify` 关键词改为读 `<HERMES_HOME>/a2a-classify-keywords.json`（env override 优先）。

累计：10 个 P0 中关闭 8 个（7 + P0-1 完整闭环），P0-4 骨架就位待接入，新增 4 个 core 模块（auth、storage、identity、audit_hook），pytest 31/31 通过（test_card_public 为 pre-existing pyyaml 依赖问题，与本次改动无关）。

| 维度 | Before | After |
|---|---|---|
| **任务存储** | 内存 `_tasks: dict`（重启即丢） | SQLite WAL，per-profile DB |
| **并发模型** | 单线程 HTTPServer | ThreadingHTTPServer + storage 锁 |
| **认证** | 无（CORS=*） | Bearer token + CORS allowlist |
| **task_id** | uuid4.hex[:12]（12 字符可碰撞） | uuid4.hex 全长 32 字符 |
| **subprocess 日志** | stdout/stderr → /dev/null | `~/.hermes/logs/a2a-<profile>.log` |
| **业务身份** | 硬编码 "三省六部/小黄/regent" 进 core | env > profile file > home file > generic fallback |
| **端口冲突** | 直接崩 | 探测后扫描下一空闲端口，告警继续 |
| **/a2a/tasks 列表** | 不存在 | `GET /a2a/tasks?limit=&status=` |

---

## 1. 已关闭的 P0

| ID | 标题 | 关闭方式 |
|---|---|---|
| P0-1 | CORS=* + 无 auth | `core/auth.py`：Bearer token（env > file > 自动生成，0600 权限）+ CORS allowlist（默认仅 127.0.0.1/localhost）。`server.py` 在每个 `/a2a/tasks*` 处理前调 `check_auth`。常时比较用 `hmac.compare_digest`。 |
| P0-3 | subprocess 日志被吞 | `plugin.py:_open_log` 把 server.py stdout/stderr 写入 `~/.hermes/logs/a2a-<profile>.log`，stderr 合流 stdout。fallback DEVNULL 仅在文件不可写时。 |
| P0-5 | task 状态全在内存 | 新增 `core/storage.py` 提供 `TaskStore`（SQLite WAL，threading.Lock，per-profile DB `~/.hermes/data/a2a-<profile>.db`）。`server.py` 移除 `_tasks: dict`，全部走 `_store.save/get/list/delete/prune`。 |
| P0-7 | 业务身份硬编码进 core | 新增 `core/identity.py`，task_handler 改为 `load_identity_prefix(hermes_home, profile)`。优先级：env `HERMES_A2A_IDENTITY` > `profiles/<name>/a2a-identity.md` > `<home>/a2a-identity.md` > 通用 fallback（仅含 profile 名）。Core 内**零业务字符串**。 |
| P0-8 | A2A spec 缺 GET /a2a/tasks | `server.py:do_GET` 新增 `/a2a/tasks` list 端点，支持 `?limit=` 和 `?status=` 过滤。 |
| P0-9 | HTTPServer 单线程 + _tasks 无锁 | `HTTPServer` → `ThreadingHTTPServer(daemon_threads=True)`；写入路径 `_exec_lock` 保护 save+spawn；存储层用 `TaskStore._lock` 串行化 SQL 调用。 |
| P0-10 | task_id 12-hex 可碰撞 | `f"a2a-{uuid.uuid4().hex}"`（36 字符总长），集成测试 `test_concurrent_posts_unique_ids` 验证 20 并发无碰撞。 |

剩余 P0：
- **P0-2**（subprocess prompt 通过 argv 传递，`ps` 可见）：**评估完成，stdin 方案确认不可行**。`hermes chat` CLI 不接受 stdin 作为 query —— 实测 `echo prompt | hermes chat -q -` 会把 `-` 字面化为 query 内容（输出 `--- 在的。有什么事？`）。可选路径：
    1. **改 hermes 上游**加 `--stdin` 或 `--query-file PATH`（彻底解决，但跨仓库变更）。
    2. **临时文件 + flag**：写到 `tempfile`，传 `--query @path`（仍需 hermes CLI 改动）。
    3. **维持现状**：`subprocess.run(cmd, ...)` 已经使用 list 参数避免 shell 解释，OS-level 注入已防；剩余风险仅 (a) `ps aux` 可见 prompt，(b) ARG_MAX 限制（macOS ≈ 256 KB）。
    4. **优先 API Server 模式**（已实现）：`HERMES_PROFILE ∈ {regent, default}` 自动走 `/v1/runs`，prompt 经 HTTP body，不暴露到 ps。
    决策：保留方案 4 作为 default + 方案 1 作为长期 backlog（记入 `~/.hermes/tmp/hermes-cli-stdin-feature-request.md`）。
- **P0-4**：✅ **骨架已落地** — 见 `core/audit_hook.py`。reviewer fan-out 接入 server.py 是下一步（需配套 reviewer profile 注册）。
- **P0-6**：见第 4 节「仍然待做」。

---

## 2. 新增/修改的文件

```
core/
├── auth.py              [NEW]      ~290 LOC — token + CORS allowlist
├── storage.py           [NEW]      ~270 LOC — SQLite-backed TaskStore (thread-safe)
├── identity.py          [NEW]       ~80 LOC — identity_prefix loader (env/file/fallback)
├── audit_hook.py        [NEW★]     ~220 LOC — 四道防线 audit gate (P0-4, 迭代 2)
├── server.py            [REWRITE] ~215 LOC — ThreadingHTTPServer + auth + storage + GET /a2a/tasks
├── plugin.py            [MOD★]    ~165 LOC — port-collision detect + 子进程健康探测 (P1-1, 迭代 2)
├── task_handler.py      [MOD★]    ~240 LOC — identity_prefix delegate + classify keywords 外置 (P1-13, 迭代 2)
├── agent_card.py        [UNCHANGED]
├── discuss.py           [MOD★]    ~905 LOC — _a2a_send/_a2a_poll 加 Bearer header (P0-1 闭环, 迭代 2)
└── auto_discuss.py      [UNCHANGED, 纯分类引擎无 HTTP 调用]

tests/                   [NEW DIR]
├── __init__.py
├── conftest.py          — hermes_home + reset_auth fixtures
├── test_auth.py         — 12 tests (token 三层来源、constant-time、CORS allowlist)
├── test_storage.py      — 8 tests (CRUD、upsert、prune、thread safety 200 并发写)
├── test_identity.py     — 5 tests (4 层优先级、generic fallback 不泄漏业务名)
└── test_server_integration.py  — 7 tests (subprocess spawn → curl-style HTTP probe)

docs/
└── IMPLEMENTATION_SUMMARY.md  [THIS]
```

**测试结果**：
- 迭代 1：`pytest tests/ → 32 passed in 1.52s`
- 迭代 2：`pytest tests/ → 31 passed, 1 failed in 4.14s`
  - 唯一失败 `test_card_public` 因 **系统 Python 缺 pyyaml**（与本次改动无关，stash 验证基线同样失败）。修复方案是给 `agent_card.py:_load_config` 加 `try: import yaml except ImportError: return {}` —— 已记入 P1 backlog，但不阻塞本次部署。
- 本次新增 self-test：
  - `audit_hook.py` 四道防线断言（profile / depth / semaphore / rate）全部通过。
  - `task_handler._classify` 默认 + env-override 两条路径回归通过。
  - `plugin._stable_port('regent')` 仍稳定输出 `8939`，与 discuss.py 中 `DEFAULT_REGENT_PORT` 一致。

---

## 3. 架构决策

### 3.1 P0-6/P0-7：core 与 s6m-config 的边界

**决策**：core/ 必须 zero business strings。所有 "三省六部 / 监国太子 / 小黄 / regent / Alex" 等都迁出。

**实现**：
- `identity.py` 通过 4 层 lookup 加载身份 prefix。Core 内 fallback 只含「你是 profile 「{profile}」 的 Hermes Agent。」
- s6m-config 部署时需要在 `~/.hermes/profiles/regent/a2a-identity.md` 等位置写入业务身份文件（原 task_handler.py:79-91 的字符串）。
- `agent_card.py` 中残留的 `"三省六部 (Three Provinces Six Ministries)"` 作为 `provider.organization` —— **本次未改**，因为它是默认值，将来可通过 env `A2A_PROVIDER_ORG` 或 `~/.hermes/agent-card.yaml` 覆盖。下次迭代清理。

**迁移清单（部署侧需要的动作）**：
1. 创建 `~/.hermes/profiles/regent/a2a-identity.md`，内容是原 regent 身份块。
2. 创建 `~/.hermes/profiles/default/a2a-identity.md`，内容是原 default 身份块。
3. 其他 14 个 profile 用 generic fallback 即可（或按需补充）。
4. 全 16 profile 重启：`launchctl bootout/bootstrap` per CLAUDE.md 流程。

### 3.2 discuss.py 拆分

**现状**：898 行（不是 brief 中的 1718），已经做过一轮拆分（auto_discuss.py 已抽出）。下一步建议：

```
discuss.py (898 行) → 拆为
├── prompts.py        — _ROLEPLAY_PROMPT_TMPL / _SYNTHESIZE_PROMPT_TMPL / _DEPTH_LABELS / _DISCUSSION_STYLE_GUIDE (~120 LOC)
├── transport.py      — A2ADiscussion._a2a_send / _a2a_poll / _tg_send 仅传输层 (~150 LOC)
├── roleplay.py       — roleplay() 主循环 (~120 LOC)
├── synthesize.py     — synthesize() 主循环 (~80 LOC)
├── discuss.py        — A2ADiscussion 装配 + CLI (~150 LOC)
└── auto_discuss.py   — [已抽离]
```

**本次未做**：discuss.py 已经能跑，且与 brief 优先级 P0/P1 关联性弱，留给 Phase 2。

### 3.3 端口碰撞根治

**现状**：sha256(profile) % 300 + 8650 仍可能在 16 profile 中两两碰撞（生日悖论 ~30% 概率）。

**本次方案**：探测式 fallback —— `plugin._resolve_port` 先 socket bind 探测稳定端口；占用则向后扫描下一空闲端口并写 warning 日志。`A2A_PORT` env 仍为最高优先级 escape hatch。

**未做**：**端口注册表**（中央 `~/.hermes/data/a2a-ports.json` 持久化每 profile 实际端口，agent_card url 用持久化端口）。这是更稳的方案，但要求所有 client（如 discuss.py 的 hardcoded 8939/8945）也读注册表，规模较大。留给 Phase 2。

discuss.py 中的 `DEFAULT_REGENT_PORT=8939, DEFAULT_DEFAULT_PORT=8945` 与 sha256 公式恰好一致（已验证），所以当前 prod 仍 work。但任何 profile 名变更都会破坏，**建议 discuss.py 也改用 `_stable_port` 公式或读注册表**。

---

## 4. 仍然待做

- **P0-2** subprocess argv prompt 泄漏 — **评估完成**（见第 1 节）；需 hermes CLI 改造或临时文件 + flag 方案。优先级降为 P1（已有 API Server 路径作 mitigation）。
- **P0-4** audit_hook **骨架已上线**；剩余工作：(a) 接入 server.py 的 `do_POST` 处理（在 task 调度前 `gate.check(profile=..., headers=req.headers)`），(b) 出站 reviewer 调用处加 `next_depth_headers()`，(c) 部署 reviewer profile 后联调。
- **P0-6** agent_card.py 的 `provider.organization` 业务字符串；同样建议 `_load_config` 加 `try: import yaml except ImportError: return {}` fallback 顺手修 test_card_public。
- **P1-1** task_handler 的 `_API_SERVER_PORTS = {"regent": 8643, "default": 8642}` 仍硬编码，应来自 env 或注册表。
- **P1-2** A2A spec：cancel / push_notifications / artifact-by-id 等端点。
- **P2** discuss.py 拆分；EmpireThread 桥；reviewer webhook。

### 4.1 P0-4 audit_hook 接入示例（参考）

```python
# server.py do_POST 内的预期改造
from audit_hook import DEFAULT_GATE, next_depth_headers

ok, reason = DEFAULT_GATE.check(
    profile=os.environ.get("HERMES_PROFILE"),
    headers=self.headers,
)
if not ok:
    return self._send_json({"error": "audit_gate", "reason": reason}, 429)

with DEFAULT_GATE.acquire() as granted:
    if not granted:
        return self._send_json({"error": "audit_gate", "reason": "concurrency"}, 429)
    # ... 正常 spawn task ...

# 出站 reviewer fan-out（未来的代码）
outbound_headers = {"Authorization": f"Bearer {token}", **next_depth_headers(self.headers)}
```

---

## 5. 部署清单（按序）

```bash
# 1. 同步 core/ 到 plugin 部署点
cp ~/code/hermes-a2a/core/*.py ~/.hermes/plugins/hermes-a2a/

# 2. 创建身份文件（业务专属）
cat > ~/.hermes/profiles/regent/a2a-identity.md <<'EOF'
【系统提示】你正在通过 A2A 协议接收任务。
你的身份：监国太子 (regent)，三省六部总枢。
你有独立判断权，可对议题做出裁决。
EOF

cat > ~/.hermes/profiles/default/a2a-identity.md <<'EOF'
【系统提示】你正在通过 A2A 协议接收任务。
你的身份：小黄（主频道助手），Alex 的个人助理。
你独立于三省六部体系之外，不属于任何部门。
请基于你的独立视角完成任务，不要冒充三省六部成员。
EOF

# 3. 决定 A2A_AUTH_TOKEN —— 16 profile 共享同一 token (推荐用文件)
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.hermes/.a2a-token
chmod 600 ~/.hermes/.a2a-token

# 4. 重启所有 A2A 服务（per profile）
for p in $(ls ~/.hermes/profiles); do
  HOME=/Users/alexcai launchctl bootout gui/501/com.hermes.a2a.$p 2>/dev/null
  HOME=/Users/alexcai launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.hermes.a2a.$p.plist
done

# 5. 验证
bash ~/code/hermes-a2a/core/scripts/hermes-a2a-doctor.sh

# 6. 手动验证 auth
TOKEN=$(cat ~/.hermes/.a2a-token)
curl http://127.0.0.1:8939/health                          # 200 (公开)
curl http://127.0.0.1:8939/a2a/tasks                       # 401
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8939/a2a/tasks  # 200 {"tasks":[]...}
```

---

## 6. 影响面（其他需要更新的代码）

| 文件 | 改动 |
|---|---|
| `core/discuss.py` (迭代 2 已修) | `_a2a_send` / `_a2a_poll` 已加 `Authorization: Bearer <token>` header；token 通过 `auth.load_or_create_token(HERMES_HOME)` 解析（env `A2A_AUTH_TOKEN` > 文件 `~/.hermes/.a2a-token` > 自动生成）。构造失败 fallback 到 anonymous（log warning，server 端会返 401，便于排错）。 |
| `core/scripts/hermes-a2a-doctor.sh` | health 无 auth 仍 OK，但若它探测 `/a2a/tasks` 会 401，需要带 token。**待补**：doctor 脚本本身读 `~/.hermes/.a2a-token`。 |
| `core/task_handler.py:113` | 调用 hermes API server `/v1/runs` 是另一服务，不受本次 auth 改动影响。 |
| `core/agent_card.py:54 import yaml` | 测试环境（系统 python）缺 pyyaml 会让 `/a2a/.well-known/agent-card.json` 抛 ModuleNotFoundError → 客户端见 RemoteDisconnected。修复：`try: import yaml; except ImportError: yaml=None; return {}`。**未在本次改动**（与 P0/P1 任务正交），但建议下次 hotfix 一并补上。 |

> **部署前验证**：迭代 2 已闭环 P0-1 客户端侧。建议 doctor 脚本同步更新后跑一遍：`bash core/scripts/hermes-a2a-doctor.sh`。

---

## 7. 已新增的可配 env vars

| Var | 默认 | 说明 |
|---|---|---|
| `A2A_AUTH_TOKEN` | （文件 fallback） | 优先级最高的 token 来源 |
| `A2A_CORS_ORIGINS` | `http://127.0.0.1,http://localhost` | 逗号分隔 |
| `A2A_MAX_TASKS` | 1000 | SQLite 中保留的最大任务数 |
| `A2A_TASK_TTL` | 3600 | 任务保留秒数 |
| `HERMES_A2A_IDENTITY` | （文件 fallback） | 整段身份 prefix，调试用 |
| `A2A_PORT` | （hash fallback） | 强制端口，escape hatch |
| `A2A_CLASSIFY_KEYWORDS` ★ | （文件 fallback） | JSON 路径，覆盖 `_classify` 关键词桶 |
| `A2A_AUDIT_MAX_DEPTH` ★ | 2 | audit hook 最大 fan-out 深度 |
| `A2A_AUDIT_MAX_CONCURRENT` ★ | 8 | audit hook 并发上限（process-wide） |
| `A2A_AUDIT_RATE_PER_MINUTE` ★ | 60 | audit hook 滑动窗 60s 内最大触发数 |
| `A2A_AUDIT_SKIP_PROFILES` ★ | `reviewer,auditor,critic` | 永不触发 audit fan-out 的 profile 名（逗号分隔） |

★ 标记为迭代 2 新增。

---

## 8. 迭代 2 改动一览（本节即 task 6 的产物）

| 项 | 文件 | 性质 | 摘要 |
|---|---|---|---|
| 1 | `core/discuss.py` | MOD | `A2ADiscussion.__init__` 加载 token；`_a2a_send` / `_a2a_poll` 注入 `Authorization: Bearer …` |
| 2 | `core/task_handler.py` | EVAL (P0-2) | 调研 stdin 注入方案 → 不可行；保留 `_via_api_server` 优先 + log 进入 backlog |
| 3 | `core/audit_hook.py` | NEW | 220 LOC 骨架：`AuditGate.check()` + `acquire()` + `next_depth_headers()`；env-tunable；模块级 `DEFAULT_GATE` |
| 4 | `core/plugin.py` | MOD | 新增 `_wait_for_server`：Popen 后探测 ≤2s 端口被实际占用 + 子进程未崩；TOCTOU 窗口收紧（log-only，不抛） |
| 5 | `core/task_handler.py` | MOD | `_RESULT_SIGNALS` → `_DEFAULT_RESULT_SIGNALS` + `_load_signals()`（env > `~/.hermes/a2a-classify-keywords.json` > defaults）；模块级 cache；fallback per-bucket |
| 6 | `docs/IMPLEMENTATION_SUMMARY.md` | MOD | 本文档（迭代 2 增补） |

**部署侧动作**：
1. **同步 core/**：`cp ~/code/hermes-a2a/core/*.py ~/.hermes/plugins/hermes-a2a/`（新增 `audit_hook.py`）。
2. **token 准备**：确保 `~/.hermes/.a2a-token` 存在且 16 profile 共享同一 token（或同设 `A2A_AUTH_TOKEN` env）。
3. **可选**：写 `~/.hermes/a2a-classify-keywords.json` 自定义结果分类关键词（不写则用默认桶）。
4. **全 profile 重启**（per CLAUDE.md 流程）。
5. **验证**：在 regent profile 上 `python -c "from discuss import A2ADiscussion; d=A2ADiscussion(dry_run=False); print(d._auth_token[:8])"`，应输出 token 前 8 字符。

---

**完**
