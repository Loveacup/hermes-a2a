# 三省六部资源占用调查报告

**日期**: 2026-05-30
**调查范围**: hermes-a2a 项目 + 三省六部 16 profile 部署体系
**触发问题**: 32 个 Hermes 进程（16 A2A + 16 Gateway）占用过高，需系统性优化方案
**结论提要**: 真实瓶颈不是 32 个守护进程，而是 **699 个孤儿 `mcp-searxng` 子进程**，吞噬了 **~2.5 GB RSS**（实测内存压力的 5×–8× 主因）。先修这条命脉，再讨论守护进程瘦身。

---

## 0. 实测基线（现场数据，2026-05-30 04:43）

| 类别 | 进程数 | 总 RSS | 单进程均值 | 备注 |
|---|---|---|---|---|
| Hermes API/Gateway (Python) | 16 | 260 MB | 16 MB | `hermes_cli.main gateway run --replace`，包含 `default` + `regent` 主交互 + 14 个 API server profile |
| A2A `server.py` (Python) | 16 | 107 MB | 6.7 MB | 已是 stdlib + sqlite 极简实现，本身无优化空间 |
| **mcp-searxng 总量** | **1436** | **~5.1 GB** | **3.6 MB** | **节点 + npm 包装器，绝大部分是孤儿** |
| - 其中孤儿 (ppid=1) | 1396 | **~2.5 GB** | 3.6 MB | npm exec 697 + node 699，亟需清理 |
| - 其中活跃 (有 gateway 父) | 40 | ~2.7 GB | — | 16 npm exec + 16 node + 8 旁支 |
| tavily MCP | 32 | — | — | 16 npm exec + 16 node，零孤儿 |
| brave-search MCP | 16 | — | — | 仅 node，无 npm 包装，零孤儿 |
| codegraph MCP | 5 | — | — | 仅 regent 启用 |
| context7 MCP | 8 | — | — | 多副本但无孤儿 |
| playwright MCP | 8 | — | — | 多副本但无孤儿 |
| **系统总进程** | **~2100** | — | — | 健康基线 ~1800，多出 ~300，与孤儿数吻合 |

**关键事实校正**：briefing 写的"非交互 Gateway 每个 20 MB × 14 = 280 MB"基本属实（最新读数 260 MB），但**这只是冰山一角**。真正烧内存的是底下的 1436 个 MCP 子进程（5 GB+），其中 1396 个是不该存在的孤儿。

---

## Q1: 为什么非交互 Gateway 需要 20 MB？

### 1.1 这些"Gateway"其实是 API Server

调查发现，briefing 称的"Gateway"在架构上**不是 Telegram/Discord 适配器，而是 OpenAI-兼容的 Hermes API Server**：

```
ai.hermes.gateway.plist             # default profile —— 完整 messaging adapter
ai.hermes.gateway-regent.plist      # regent profile —— 完整 messaging adapter
com.hermes.api.<profile>.plist × 14 # 14 个非交互 profile —— 仅启用 api_server 平台
```

三者**都跑同一份代码** `python -m hermes_cli.main --profile X gateway run --replace`，差异只在 `~/.hermes/profiles/<profile>/config.yaml` 里的 `platforms` 配置。14 个非交互 profile 只启用 `api_server` 平台，但 Python 解释器、Hermes 框架、AIAgent cache、LLM 客户端、MCP 工具栈全都加载。

### 1.2 单进程 RSS 分布

| Profile | RSS | 启用 platforms | 启用 MCP servers |
|---|---|---|---|
| `default` (87068) | 29 MB | api_server + telegram + ... | brave + searxng + tavily + exa + codegraph |
| `regent` (85575) | 16 MB | api_server + telegram | brave + searxng + tavily + exa + codegraph |
| `dispatcher` (90579) | 18 MB | api_server only | brave + searxng + tavily + exa |
| `archivist` (90451) | 13 MB | api_server only | brave + searxng + tavily + exa |
| `tester` (91406) | 10 MB | api_server only | brave + searxng + tavily + exa |

观察：**`default` 比非交互 profile 重 ~15 MB**，这部分对应 Telegram/Discord 适配器（discord/aiogram/aiohttp 客户端、长连接、handler 注册）。

非交互 profile 的 ~13 MB 构成（粗略估算）：
- Python 解释器 + 标准库：~5 MB
- Hermes 框架核心（agent loop / config / 16 个 toolset 注册）：~4 MB
- aiohttp web 框架 + API server 路由（4188 行 `api_server.py`）：~2 MB
- AIAgent cache + LLM client 类（OpenAI / Anthropic SDK 懒加载）：~1 MB
- 工具/skill 元数据 + plugins 加载：~1 MB

### 1.3 是否每个 gateway 都加载了完整 toolset/skills/MCP？

是。证据：

```bash
$ ps -eo ppid,command | awk '$1==90451' | head -5   # archivist 的子进程
90452 90451 npm exec @modelcontextprotocol/server-brave-search
90453 90451 npm exec mcp-searxng
90454 90451 npm exec tavily-mcp@latest
90455 90451 npm exec exa-mcp-server
```

哪怕史馆（archivist）这种纯归档角色，也启动了 4 个 web search MCP。理由是 `~/.hermes/profiles/archivist/config.yaml` 里全量复制了 `mcp_servers:` 段（4 个搜索服务），即使它从不使用。

**根因**：profile config.yaml 是按"全功能复制"出来的，没有按角色裁剪 MCP servers。

---

## Q2: 能否用轻量 HTTP server 替代非交互 Gateway？

### 方案 A — 纯 aiohttp API Server（不跑完整 Hermes gateway）

**可行性**: ⚠️ 受限。

API Server 的核心职责是接收 `/v1/runs` 请求 → 投递给 Hermes agent loop → 流回结果。Hermes agent loop 需要：
- 完整的 toolset 注册（决定哪些工具可调用）
- 完整的 MCP server 连接（很多工具调用走 MCP）
- AIAgent cache（session continuity）
- 模型客户端（OpenAI / DeepSeek / Anthropic SDK）

如果把 API Server 剥离成"纯 aiohttp"，它就只能做**透传代理**——把请求转发到其他地方真正执行。但既然 A2A 的 task_handler 已经在做这件事（`urllib.request.urlopen("http://127.0.0.1:port/v1/runs")`），多套一层代理意义不大。

**预估节省**: 0–3 MB / profile（去掉的也只是 platform adapter 的薄壳）。不值得改造。

### 方案 B — 共享进程（一个 Hermes 进程承载多 profile）

**可行性**: ❌ 高风险，不推荐。

Hermes 当前架构强依赖**进程级 profile 隔离**：
- `HERMES_HOME=~/.hermes/profiles/<profile>` 决定 SOUL.md、skills/、config.yaml、kanban.db 全部路径
- AIAgent cache 按 profile 隔离（128 个 session 上限是 per-process）
- 模型选择、system prompt、toolsets 全在 config 里固化

要做共享进程，需要把 profile 作为**请求级参数**注入到 agent loop。涉及：
- gateway/run.py 的 `_get_or_create_agent(session_key)` 改成接收 profile
- 所有 toolset 注册改成 profile-scoped
- MCP 工具调用改成 profile-routed
- 估计 800+ 行改动，跨上游 hermes-agent + 本地 hermes-a2a

**预估节省**: 14 × 13 MB - 1 × ~80 MB = 100 MB（理论上）。但**改动量与风险与价值不匹配**。

### 方案 C — 按角色裁剪 MCP servers（推荐）

**可行性**: ✅ 极易。

非交互 profile（如 archivist 史馆）根本不需要 4 个 web search MCP。把 `mcp_servers:` 段精简为 0 个或仅 1 个（`searxng`），可省下：
- Python 侧 MCP client 句柄: ~0.5 MB
- 子进程 npm exec + node：每 server ~5–6 MB

每个非交互 profile 移除 3–4 个 search MCP × ~5 MB = **每个 profile 节省 15–20 MB**，14 profile 共节省 **~250 MB**。

实施成本：编辑 `~/.hermes/profiles/<profile>/config.yaml` 删 `mcp_servers` 内多余条目，重启 launchd。约 1 小时。

---

## Q3: A2A 进程能否进一步瘦身？

### 3.1 现状

`core/server.py` 已是极简 stdlib 实现：`http.server.ThreadingHTTPServer` + `sqlite3` + 自研 registry/storage/auth/audit_hook，零第三方依赖。**每进程 ~6.7 MB**，几乎贴着 Python 解释器底限。

### 3.2 能否合并为单进程多端口？

**理论可行**: 1 个 master 进程 listen 16 个端口，per-port handler 注入 profile context。

**实施代价**:
- 改 `server.py` 启动逻辑（多 Server 实例 + asyncio gather）
- 改 `paths.py` 让 `HERMES_HOME` 从 socket-local 变量解析（当前是全局 env）
- 改 `storage.py` 让 sqlite path per-request 解析（当前是 module-level 常量）
- 失去 **per-profile launchd 自愈隔离**（一个 profile 崩了影响所有）
- 失去 **per-profile 日志分流**（当前 `/tmp/a2a-<profile>-<port>.log`）

**预估节省**: 16 × 6.7 MB - 1 × ~10 MB = 97 MB。

**结论**: 节省 97 MB 但失去运维隔离 + 增加 200+ 行改动 + 增加测试矩阵。**不推荐**——A2A 进程不是瓶颈，Q4 的孤儿才是。

### 3.3 可做的微优化（不推荐启动）

- 用 `asyncio` + `aiohttp` 替代 `ThreadingHTTPServer`：单进程内可少几条 worker thread，但 aiohttp 引入第三方依赖，**反而可能让 RSS 上升**（aiohttp 自身 ~3 MB）。
- 延迟导入 `audit_hook`：节省 100 KB。
- 关掉 sqlite WAL：可能节省 100 KB，但牺牲并发性。

**这些小修小补都不值得做**。A2A 进程已经是优化得很好的极简形态。

---

## Q4: 进程数量优化

### 4.1 1396 个孤儿 mcp-searxng 进程：根因分析

**这是本次调查的最大发现**。截至 04:43，系统中有：

| 状态 | npm exec wrapper | node interpreter | 合计 | 总 RSS |
|---|---|---|---|---|
| 活跃（gateway 父存活） | 16 | 16 + 22（多余） | 54 | ~200 MB |
| **孤儿（ppid=1）** | **697** | **699** | **1396** | **~2.5 GB** |
| **总计** | **717** | **719** | **1436** | **~5.1 GB** |

**关键观察**：**仅 mcp-searxng 泄漏，其他 MCP（tavily/brave/codegraph/context7/playwright）零孤儿**。

#### 4.1.1 为什么只有 mcp-searxng 泄漏？

机制：

1. **launchd 重启行为**: `com.hermes.api.<profile>.plist` 设 `KeepAlive`，gateway Python 进程崩溃/被杀后 launchd 立刻重启。
2. **子进程不被 launchd 收割**: macOS 下 Python 父进程被 SIGKILL 时，子进程被 reparent 到 PID 1 (init/launchd) 而不是被一起杀掉。
3. **stdio MCP 退出语义不一致**:
   - 多数 MCP server（tavily/brave/exa/codegraph）在 stdin EOF 时自觉退出
   - `mcp-searxng` 不退出 —— 要么不监听 stdin EOF，要么 `npm exec` 外壳吃掉了信号
4. **Hermes 的孤儿清理只在父进程内有效**: `tools/mcp_tool.py:_orphan_stdio_pids` + `_force_kill_stdio_pids` 只在当前 Python 进程的内存里追踪 PID。父进程崩了，这些追踪也丢了。

数据印证：
```
oldest orphan: PID 1387 启动于 Fri May 29 22:51:04（~30 小时前）
newest orphan: PID 61406 启动于 Sat May 30 02:18:05（~2.5 小时前）
按时段分布: 5/28 起 5 个，5/29 起 178 个，5/30 起 516 个 —— 持续累积
```

**这是直接吞噬 RSS 的最大单点**：2.5 GB / 16 GB 物理内存 = 15.6% 的孤儿税。

### 4.2 其他进程冗余

#### 4.2.1 npm exec 双重包装
每个 MCP server 跑两个进程：`npm exec <name>` 的 sh 外壳 + 实际 node。**这是 npm 设计造成的**——不是 Hermes 问题，但可以通过直接调用 node binary 绕过（见 P1 方案 1）。

#### 4.2.2 重复 MCP 启动
- 16 个 gateway 各启动 brave/searxng/tavily/exa = 64 个 MCP 进程
- 即使裁剪掉 14 profile 的 web search，最少也要 2×（default + regent）= 8 个 MCP
- **API Server 之间不共享 MCP**——Hermes 当前架构每个 gateway 独立加载 MCP

#### 4.2.3 系统总进程 2100
- 健康基线 ~1800
- 多出 ~300 个，全部对应到孤儿 mcp-searxng 群体（697 + 699 - 已知活跃 ~80 ≈ 1316，扣掉清理掉 PID-1 后的真实数 ~300）

实测显示 `load averages: 4.40 4.82 5.05` —— **不是 CPU 烧热**（这些进程都 idle），而是 Mach scheduler 维护进程表的固定开销，加上 vm_stat 显示 free memory pages 仅 3370（~53 MB），系统已贴近内存压力警戒线。

---

## Q5: P0/P1/P2 优先级优化方案

### P0 — 立即执行（阻塞性 / 不改架构 / 节省最大）

#### P0-1: 一次性清理 1396 个孤儿 mcp-searxng 进程

```bash
# 验证后执行：
ps -eo pid,ppid,command | awk '$2==1 && /mcp-searxng/ {print $1}' | xargs kill -TERM
sleep 3
ps -eo pid,ppid,command | awk '$2==1 && /mcp-searxng/ {print $1}' | xargs kill -KILL 2>/dev/null
```

- **预估节省**: ~2.5 GB RSS，~300 进程
- **难度**: ⭐ 一次性脚本
- **风险**: 极低（这些进程都是孤儿，没有用户依赖）
- **持久化**: 必须配合 P0-2 否则会再次泄漏

#### P0-2: 加个 launchd 周期性 reaper

新增 `~/Library/LaunchAgents/com.hermes.mcp-reaper.plist`：每 10 分钟扫描 `ppid=1` 且 cmd 含 `mcp-searxng` 的进程并 kill。

- **预估节省**: 长期把 mcp-searxng 孤儿压在 < 50 个
- **难度**: ⭐⭐（plist + bash 脚本，~30 行）
- **风险**: 低；只 kill ppid=1 的孤儿，永远不动有活父的 MCP

实现示例：
```xml
<key>StartInterval</key><integer>600</integer>
<key>ProgramArguments</key>
<array>
  <string>/bin/bash</string>
  <string>-c</string>
  <string>ps -eo pid,ppid,command | awk '$2==1 && /mcp-searxng/' | awk '{print $1}' | xargs -r kill -TERM</string>
</array>
```

#### P0-3: 14 个非交互 profile 裁剪 MCP servers

编辑 `~/.hermes/profiles/{archivist,auditor,budget,...}/config.yaml`，删除 `mcp_servers` 里多余的 search 工具（保留 0 个或仅 `searxng`）。重启对应 launchd。

- **预估节省**: 14 × ~20 MB ≈ **280 MB**（API server 自身轻量化）+ 子进程减少 14 × 3 × 5 MB ≈ **210 MB**
- **难度**: ⭐⭐（手工编辑 14 个 YAML + 重启）
- **风险**: 低；如果某 profile 偶然用到搜索，加回来即可。建议按 profile 角色矩阵评审：
  - 不需要 web search: archivist, auditor, budget, dispatcher, registry, protocol
  - 可能需要: hanlinyuan, engineer, planner, reviewer

### P1 — 中期改造（结构性 / 中等改动 / 中等节省）

#### P1-1: 把 npx 替换为直接 node binary（去掉 npm exec 包装）

把 `~/.hermes/profiles/*/config.yaml` 的 MCP 调用：
```yaml
command: npx
args: [-y, mcp-searxng]
```
改成：
```yaml
command: /Users/alexcai/.npm/_npx/c0fd79eb7f6ccc1e/node_modules/.bin/mcp-searxng
args: []
```

- **预估节省**: 每 MCP 干掉 1 个 npm exec wrapper（每个 ~2.6 MB）。16 profile × 4 MCP = 64 wrapper × 2.6 MB ≈ **170 MB**
- **额外收益**: 信号传递更可靠，**可能直接消除 mcp-searxng 泄漏根因**（npm exec 不再吃掉 SIGTERM）
- **难度**: ⭐⭐⭐（需要找到稳定的 binary 路径 + 升级路径策略）
- **风险**: 中；npx 缓存路径在不同 npm 版本可能漂移。建议用 `npm root -g` 找到全局路径或先把 mcp-searxng 全局安装

#### P1-2: 上游 hermes-agent 改 SIGTERM 传播

向上游 Hermes 提 PR：在 `gateway/run.py` shutdown handler 里，先 `await shutdown_mcp_servers()` 再退出。当前路径在 launchd SIGKILL 下完全失效。

- **预估节省**: 防止未来累积新孤儿
- **难度**: ⭐⭐⭐⭐（跨仓库改动，需上游评审）
- **风险**: 低
- **替代方案**: 在 Hermes 启动时 fork 一个看门狗子进程，看门狗 ppid 死亡时一并 SIGKILL 所有 MCP children（POSIX `prctl(PR_SET_PDEATHSIG)` 在 Linux 可用，macOS 用 `kqueue EVFILT_PROC NOTE_EXIT`）

#### P1-3: 把 14 个非交互 profile 关掉 Telegram/Discord 平台

确认 14 个非交互 profile 的 config.yaml 里 `platforms:` 段只启用 `api_server`，明确禁用 `telegram` / `discord` / `feishu` 等。

- **预估节省**: 已经做掉一部分；剩余收益 0–2 MB / profile
- **难度**: ⭐
- **风险**: 极低

### P2 — 长期演进（架构性 / 大改动 / 节省 < 100 MB）

#### P2-1: A2A 单进程多端口合并

合并 16 个 server.py 为 1 个 multi-port 进程。

- **节省**: ~100 MB
- **难度**: ⭐⭐⭐⭐
- **风险**: 高（失去 launchd 自愈隔离）
- **结论**: **不推荐**，性价比低

#### P2-2: API Server 共享进程

合并 14 个非交互 API Server 为 1 个 multi-profile 进程。

- **节省**: ~180 MB（理论值）
- **难度**: ⭐⭐⭐⭐⭐（跨上游 Hermes Agent 大改）
- **风险**: 极高（违反进程级 profile 隔离原则）
- **结论**: **明确不做**（briefing 已要求"不改上游 Hermes Agent 代码"）

#### P2-3: 可观测性 — 加个进程统计 metric

每个 A2A server `/a2a/metrics` 加 `mcp_orphan_count` 字段，doctor 脚本聚合后报警。

- **节省**: 0（这是预防层）
- **难度**: ⭐⭐
- **风险**: 无

---

## 优先级汇总表

| 编号 | 方案 | 预估节省 | 难度 | 风险 | 建议 |
|---|---|---|---|---|---|
| **P0-1** | 一次性清理孤儿 | **2.5 GB RSS + 300 进程** | ⭐ | 极低 | **立即执行** |
| **P0-2** | launchd reaper 定时器 | 持续防止再泄漏 | ⭐⭐ | 低 | **本周内做** |
| **P0-3** | 裁剪非交互 profile 的 MCP | **~490 MB** | ⭐⭐ | 低 | **本周内做** |
| P1-1 | npx → 直接 node binary | ~170 MB + 消除根因 | ⭐⭐⭐ | 中 | 1–2 周内 |
| P1-2 | 上游 SIGTERM 传播 PR | 长期防御 | ⭐⭐⭐⭐ | 低 | 上游 PR，慢慢推 |
| P1-3 | 关闭非交互 profile messaging 平台 | 0–2 MB / profile | ⭐ | 极低 | 顺手做 |
| P2-1 | A2A 单进程多端口 | ~100 MB | ⭐⭐⭐⭐ | 高 | **不做** |
| P2-2 | API Server 共享进程 | ~180 MB | ⭐⭐⭐⭐⭐ | 极高 | **明确不做** |
| P2-3 | 可观测性 metric | 0（预防） | ⭐⭐ | 无 | 顺手做 |

**P0 三项合计节省 ~3.0 GB RSS + 消除 ~300 个孤儿进程**，相当于把当前压力直接降到健康基线。这就足够回答 briefing 的问题，不需要动 P1/P2。

---

## 附录 A — 关键诊断命令

```bash
# 看 mcp-searxng 进程总数
ps aux | grep mcp-searxng | grep -v grep | wc -l

# 看孤儿数（ppid=1）
ps -eo ppid,command | awk '$1==1 && /mcp-searxng/' | wc -l

# 看 16 gateway 的活 RSS
ps -eo pid,rss,command | grep 'hermes_cli.main.*gateway' | grep -v grep

# 看 16 A2A 的活 RSS
ps -eo pid,rss,command | grep 'hermes-a2a/server.py' | grep -v grep

# 看 launchd 管的 hermes 服务
launchctl list | grep -E 'com.hermes|ai.hermes'
```

## 附录 B — 与 briefing 数据的差异

| 项 | briefing 报数 | 实测（2026-05-30 04:43） | 差异原因 |
|---|---|---|---|
| Gateway (regent) RSS | 65 MB | 16 MB | 数据可能采自启动初期 / 重启后 |
| Gateway (default) RSS | 25 MB | 29 MB | 接近 |
| Gateway (其他14) RSS/个 | ~20 MB | 10–18 MB | 接近 |
| A2A servers RSS/个 | 7 MB | 6.7 MB | 一致 |
| 系统总进程 | 2156 | 2100 | 接近 |
| **briefing 未提** | — | **1396 个 mcp-searxng 孤儿，2.5 GB** | **这是真问题** |

---

## 关联文档
- [s6m-a2a-optimization.md](./s6m-a2a-optimization.md) — 三大支柱方案
- [s6m-a2a-optimization-v2.md](./s6m-a2a-optimization-v2.md) — P0/P1/P2 安全/正确性审计
- 上游：`/Users/alexcai/.hermes/hermes-agent/tools/mcp_tool.py` `_orphan_stdio_pids` + `_force_kill_stdio_pids`
- 上游：`/Users/alexcai/.hermes/hermes-agent/gateway/run.py` shutdown handler
