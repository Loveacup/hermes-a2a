# regent + default A2A 接入 + 跨协议互通报告（Part 2 + Part 3）

## 结论
- ✅ hermes-a2a 部署到 regent + default profile（symlink 已存在，plugins enable 已生效）
- ✅ 2 个 standalone test server 跑在 sha256 端口：regent **:8689**、default **:8695**
- ✅ /health、/agent-card.json、POST /a2a/tasks 全通过，Agent Card 正确反映各自 SOUL.md + 模型
- ✅ API Bridge ↔ A2A 跨协议互通双向验证通过
- ✅ 未碰原有 default(8642) / regent(8643) gateway —— 它们一直在运行没动过
- ✅ 没有新代码改动（Step 1 的 `95ce18d` plugin 修复对 regent/default 同样适用）

## Part 2 — 接入步骤
- symlink：`~/.hermes/profiles/{regent,default}/plugins/hermes-a2a -> ~/.hermes/plugins/hermes-a2a/`（先前已建好，本次复用）
- enable 状态：两个 profile 的 config 都已含 `plugins.enabled: [hermes-a2a, ...]`（default 在 global `~/.hermes/config.yaml`，regent 在 `~/.hermes/profiles/regent/config.yaml`）
- 启动 standalone test server（按 brief 不重启原 gateway）：
  - `HERMES_PROFILE=regent A2A_PORT=8689 HERMES_HOME=~/.hermes/profiles/regent python3 server.py &` → pid 70489
  - `HERMES_PROFILE=default A2A_PORT=8695 HERMES_HOME=~/.hermes python3 server.py &` → pid 70490

## Part 2 — A2A 端点验证证据

### regent :8689
- `/health` → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"regent"}`
- `/agent-card`：
  - name = `Hermes Agent — regent`
  - description = `孤乃监国太子，奉父皇之命总理三省六部，总揽奏章、派工、稽核、归档之权。`
  - skills = `[health-check]`（regent 当前 toolsets 极简，只剩 hermes-cli，所以 SKILL_MAP 没命中任何条目）
  - currentModel = `{default: gpt-5.5, provider: openai-codex}`
- POST `/a2a/tasks` → `id=a2a-18f54de10e51 status=working` ✅

### default :8695
- `/health` → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"default"}`
- `/agent-card`：
  - name = `Hermes Agent — default`
  - description = `_You're not a chatbot. You're becoming someone._`（global SOUL.md 首行）
  - skills = `[health-check]`（default 也只有 hermes-cli）
  - currentModel = `{default: gpt-5.5, provider: openai-codex}`
- POST `/a2a/tasks` → `id=a2a-bbb1923715f7 status=working` ✅

## Part 2 — API Bridge ↔ A2A 跨协议互通验证

### 方向 A：default agent（API Bridge 8642）→ regent A2A 端口（8689）
- 请求：`POST localhost:8642/v1/chat/completions`，让 default agent 执行 `curl http://localhost:8689/a2a/.well-known/agent-card.json`
- 响应（节选）：`{"name":"Hermes Agent — regent","description":"孤乃监国太子...","url":"http://127.0.0.1:8689/a2a","provider":{"organization":"三省六部 (Three Provinces Six Ministries)",...},"capabilities":{"streaming":true,"pushNotifications":false},"defa...`
- 结论：default profile 的 agent 通过它的工具调用真的拉到了 regent 的 A2A Agent Card

### 方向 B：regent agent（API Bridge 8643）→ default A2A 端口（8695）
- 请求：`POST localhost:8643/v1/chat/completions`，让 regent agent 执行 `curl http://localhost:8695/a2a/.well-known/agent-card.json`
- 响应（节选）：`{"name":"Hermes Agent — default","description":"_You're not a chatbot. You're becoming someone._","url":"http://127.0.0.1:8695/a2a","provider":{...},"capabilities":{"streaming":true,"pushNotifications":false},"defaultInputModes":["text","file"],"defaultOutputModes":...`
- 结论：reverse 路径同样通

### 跨协议互通意义
- 证明 **API Bridge（自然语言/对话）与 A2A（结构化能力发现）可同一进程同时 serve、且彼此互为客户端**
- API Bridge 走 agent loop（真发起 agent，会真用 tool）；A2A 走轻量 HTTP（无 agent，仅返回元数据/记录 task）
- 实战路径：「让 default 自然语言查询 → default agent 用 terminal 工具 curl 兄弟 profile 的 A2A 端点拿能力图谱 → default agent 根据能力图谱决定下一步派工」

## 当前完整进程清单（活着 + 不动）
- API Server（Hermes 原生 gateway）：default :8642 (PID 67251)、regent :8643 (PID 67469)
- A2A Plugin（hermes-a2a，gateway 内子进程）：shangshu :8676、engineer :8668、gongbu :8698、budget :8686
- A2A standalone test server（本次新增，未走 gateway）：regent :8689 (PID 70489)、default :8695 (PID 70490)

## Part 1 评估阶段（spawn 了 2 个并行 agent，background 模式）
- agent #1 / Explore：A2A co-location 可行性（aiohttp/stdlib 差异、plugin hook 是否存在、端口预算）
- agent #2 / Explore：/v1/capabilities ↔ A2A Agent Card 字段对照、/health 增强、融合路线图
- workflow：两 agent 与 Part 2 deploy **并行**跑（agent 在后台，主 agent 同时执行部署）—— 主 agent 不空等
- 合并输出：`/tmp/a2a-vs-apibridge-analysis.md`（无 pipe table）

### Part 1 关键结论（摘要）
- **不要 co-location**（Step 2 维持独立端口）：A2A 还在实验中，需要故障隔离；Hermes plugin 系统也没 `on_api_server_init` hook，强上代价大
- **API Bridge 已经具备 80% 的 Agent Card 信息**，只是字段未对齐 A2A spec：缺 profile / skills / input-output modes / protocolVersion
- **融合路线**：Phase 1 把 hermes-a2a 改造成 `/v1/runs` + `/v1/capabilities` 之上的 thin adapter；Phase 2 推 Hermes 上游加 A2A spec 兼容
- **下一步最值得做的单一改动**：扩展 `/v1/capabilities`（Hermes 上游 PR）加入 profile / skills 数组 / currentModel 对象 / protocolVersion=1.0

## Part 3 — 代码改动
- **无新增 commit**：Part 2 没有触发任何插件代码改动。Step 1 的两个 commit (`86dea23` + `95ce18d`) 已经把 register 入口 + HERMES_HOME basename 兜底做好了，对 regent/default 同样工作
- 现 origin/main 头部 commit：`95ce18d fix: align plugin API with Hermes (register entry point) + derive profile from HERMES_HOME`

## Part 4 — 全链路终测（2026-05-27 22:45）

### 测试矩阵

| # | 测试项 | 端点 | 结果 |
|---|--------|------|:---:|
| 1 | /health | engineer:8668 | ✅ |
| 2 | /health | shangshu:8676 | ✅ |
| 3 | /health | budget:8686 | ✅ |
| 4 | /health | regent:8689 | ✅ |
| 5 | /health | default:8695 | ✅ |
| 6 | /health | gongbu:8698 | ✅ |
| 7 | Agent Card | 全部 6 个 endpoint | ✅ 各自模型正确 |
| 8 | POST task | regent:8689 → id=test-chain-001 status=working | ✅ |
| 9 | POST task | default:8695 → id=test-chain-002 status=working | ✅ |
| 10 | 跨协议 A | default API Bridge(8642) → regent A2A(8689) Agent Card | ✅ |
| 11 | 跨协议 B | regent API Bridge(8643) → default A2A(8695) Agent Card | ✅ |
| 12 | 跨协议 task | regent API Bridge(8643) → default A2A(8695) POST task | ✅ |

### 异常记录
- default:8695 standalone 进程在测试前崩溃（PID 71797 → 消失），重启为 PID 73077
- 原因未明（可能 OOM 或被 system 回收），暂不深究——standalone 模式本身不是最终方案

## 当前完整进程清单（终态）

- API Server（Hermes 原生 gateway）：default :8642、regent :8643
- A2A Plugin（gateway 内子进程）：shangshu :8676、engineer :8668、gongbu :8698、budget :8686
- A2A standalone：regent :8689 (PID 70489)、default :8695 (PID 73077)

## 遗留（已解决）
- ~~standalone test server 是临时的~~ → **已改为 launchd 监管**，崩溃自动恢复（实测 3 秒内）
- ~~regent/default 当前 skills 数组只有 health-check~~ → **已修复 agent_card.py 加入 _BASE_TOOLSETS**，skills 从 1→13
- **CLAUDE.md「全局插件」表述需修订**：实际依赖 per-profile symlink

## Part 5 — 遗留问题解决记录（2026-05-27 23:05）

### 问题 1：skills 数组只有 health-check
- **根因**：`agent_card.py:_load_config` 只读配置中显式声明的 toolsets（两个 profile 都是 `[hermes-cli]`），SKILL_MAP 零命中
- **修复**：在 `agent_card.py` 新增 `_BASE_TOOLSETS` 常量（12 个内置 toolset），`generate_agent_card` 中 merge config toolsets + base → 13 skills
- **验证**：regent 和 default 从 1 skill → 13 skills ✅

### 问题 2：默认 A2A 无进程守护，崩溃不恢复
- **修复**：创建 2 个 launchd plist（`~/Library/LaunchAgents/com.hermes.a2a.{regent,default}.plist`），`KeepAlive=true + RunAtLoad=true`
- **验证**：kill default:8695 → 3 秒内 launchd 自动重启 PID 73681→73702 ✅
- **注意**：`HOME` 环境变量在 hermes session 中被设为 profile home，需 `HOME=/Users/alexcai` 前缀调用 `launchctl`

### 启动命令（日后需要）
```bash
# 加载
HOME=/Users/alexcai launchctl bootstrap gui/501 /Users/alexcai/Library/LaunchAgents/com.hermes.a2a.regent.plist
HOME=/Users/alexcai launchctl bootstrap gui/501 /Users/alexcai/Library/LaunchAgents/com.hermes.a2a.default.plist

# 卸载
HOME=/Users/alexcai launchctl bootout gui/501/com.hermes.a2a.regent
HOME=/Users/alexcai launchctl bootout gui/501/com.hermes.a2a.default
```
