# hermes-a2a/core

Hermes Agent 的 A2A (Agent-to-Agent) 协议插件内核。基于 [Google A2A Protocol](https://github.com/a2aproject/A2A) (Apache 2.0)。

这个目录是纯插件代码，**不包含任何具体部署的 profile 名、端口、组织结构**。任何 Hermes 用户都可以直接 deploy 这个内核到自己的 profile 集合。三省六部的具体部署见 [../s6m-config/](../s6m-config/)。

## 文件清单

- `plugin.py` — Hermes 插件入口 (`register()`)。被 gateway 加载时调起，spawn `server.py` 子进程
- `server.py` — A2A HTTP Server，stdlib HTTPServer，路由 `/health`、`/a2a/.well-known/agent-card.json`、`/a2a/tasks` (POST/GET)、`/a2a/tasks/{id}/stream` (SSE)
- `agent_card.py` — 从 profile 的 `config.yaml` + `SOUL.md` + SKILL_MAP 自动生成 A2A 1.0 spec 的 Agent Card
- `task_handler.py` — 双模 task 执行：profile 在 `_API_SERVER_PORTS` 映射里 → 走 Hermes 原生 `/v1/runs`；否则 → fallback 到 `hermes chat -q` subprocess
- `plugin.yaml` — 插件元数据 (name / version / kind / author / homepage / license)
- `requirements.txt` — `pyyaml`（只此一个依赖）
- `templates/a2a-launchd.plist` — macOS launchd 服务模板（`{{PROFILE}}` / `{{PORT}}` / `{{HERMES_HOME}}` 占位符）
- `scripts/hermes-a2a-doctor.sh` — 健康聚合器，可读 port-map 文件做参数化检查
- `scripts/seed-a2a-symlinks.sh` — per-profile symlink 一键创建（Hermes 在非 default profile 下扫描 `~/.hermes/profiles/<name>/plugins/`，需用 symlink 让 plugin 全局共享）

## 端口分配
- 公式：`8650 + sha256(profile) % PORT_RANGE`
- 默认 PORT_RANGE = 300（`plugin.py:7`），可改但需重新跑碰撞检测
- 必须用 sha256 而非 Python `hash()`，否则 PYTHONHASHSEED 随机化会导致端口跨进程不稳定

## 部署方式

### 方式 A：launchd 监管（推荐，独立进程，自带 KeepAlive）
- 安装：`ln -s ~/code/hermes-a2a/core ~/.hermes/plugins/hermes-a2a`（或用 cp）
- 为每个目标 profile 渲染 plist：
  - 用 `templates/a2a-launchd.plist` 替换 `{{PROFILE}}`、`{{PORT}}`、`{{HERMES_HOME}}`
  - 落到 `~/Library/LaunchAgents/com.hermes.a2a.<profile>.plist`
- 启动：`HOME=/Users/<you> launchctl bootstrap gui/<uid> ~/Library/LaunchAgents/com.hermes.a2a.<profile>.plist`
- 验证：`curl http://127.0.0.1:<port>/health`

### 方式 B：Hermes gateway 插件（无独立监管，gateway 退出 = A2A 退出）
- 安装：把整个 core/ 拷到（或 symlink 到）`~/.hermes/plugins/hermes-a2a/`
- 在 profile 的 `config.yaml` 里 `plugins.enabled` 列表加 `hermes-a2a`
- 启动 gateway 时插件 `register()` 自动 spawn server.py（端口由 sha256 公式自动计算）
- 缺点：A2A 子进程死了不会自动复活，必须重启 gateway

## A2A 端点（部署后可用）

- `GET /health` — `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"<name>"}`
- `GET /a2a/.well-known/agent-card.json` — A2A 1.0 Agent Card：name / description / skills[] / currentModel / capabilities / protocolVersion=1.0
- `POST /a2a/tasks` — body `{"id":..., "message": {...}}` → 立即返 `{"status":"working", ...}`，后台 Thread 跑 handle_task()
- `GET /a2a/tasks/{id}` — 查询 task 状态 + artifact (含 `mode: api_server | subprocess`、`duration_s`、`response`)
- `GET /a2a/tasks/{id}/stream` — SSE (当前是 stub，echo 单帧 + `[DONE]`)

## 双模执行如何选择

在 `task_handler.py` 顶部：

```python
_API_SERVER_PORTS = {"regent": 8643, "default": 8642}
```

profile 名命中此映射 → 调本地 Hermes 原生 `/v1/runs` API Server（轻量、流式、共享 session）。
否则 → fallback 到 `subprocess.run(["hermes","chat","-q",...])`，重但通用。

用户接入新 API Server 时，在自己的部署里改这个映射即可。

## SKILL_MAP — Hermes toolset → A2A skill 翻译

`agent_card.py:7-20` 定义了从 Hermes 内部 toolset 名到 A2A 标准 skill id 的字典映射。当前覆盖 12 类工具（terminal / file / web / browser / delegation / kanban / memory / vision / image_gen / code_execution / session_search / cronjob），每个 skill 含 `{id, name, description, examples, tags}` 5 字段，符合 A2A spec 1.0。

profile 的 `config.yaml` 没有 `toolsets:` 字段时，会回退到 `_BASE_TOOLSETS`（agent_card.py:23）——即 12 个 toolset 全开。

## 测试 / 健康检查

- `bash scripts/hermes-a2a-doctor.sh [--port-map PATH]` — 列出所有 A2A + API Server 端点状态 + skills 数
- `bash scripts/hermes-a2a-doctor.sh --json` — JSON 输出，方便接监控
- 单点：`curl http://127.0.0.1:<port>/health`
- 任务测试：`curl -X POST :{port}/a2a/tasks -H 'Content-Type: application/json' -d '{"id":"test","message":{"text":"echo hi"}}'`

## 许可

Apache-2.0（与上游 A2A Protocol 一致）。
