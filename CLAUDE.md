# CLAUDE.md — hermes-a2a

## 项目身份

**hermes-a2a** 是 Hermes Agent 的 A2A（Agent-to-Agent）协议实现，monorepo 结构分两层：通用内核 + 三省六部部署配置。

- 仓库：`~/code/hermes-a2a/`
- 部署：`~/.hermes/plugins/hermes-a2a/` ← symlink 或 cp 自 `core/`
- 协议：Google A2A Protocol (a2aproject/A2A, Apache 2.0)

## 三省六部背景

为三省六部治理体系（16 个 profile）提供同步实时的跨部门通信能力。之前各 profile 仅靠 Kanban 异步通信；A2A 加入后可做能力自动发现与任务同步委派。当前全 16 profile 已部署，全部 launchd 监管。

## Monorepo 架构

```
hermes-a2a/
├── core/                          # 🔧 通用 A2A 协议内核（无业务依赖）
│   ├── plugin.py                  # Hermes 插件入口 (register)
│   ├── server.py                  # A2A HTTP Server
│   ├── agent_card.py              # Agent Card 自动生成
│   ├── task_handler.py            # 双模任务执行
│   ├── plugin.yaml                # 插件元数据
│   ├── discuss.py                  # 讨论编排引擎（ROLEPLAY + SYNTHESIZE）
│   ├── requirements.txt           # pyyaml
│   ├── __init__.py                # from .plugin import register
│   ├── templates/
│   │   └── a2a-launchd.plist      # {{PROFILE}}/{{PORT}}/{{HERMES_HOME}} 占位符
│   ├── scripts/
│   │   ├── hermes-a2a-doctor.sh   # 健康聚合（支持 --port-map）
│   │   └── seed-a2a-symlinks.sh   # per-profile symlink 种子
│   └── README.md                  # 内核文档（面向通用 Hermes 用户）
│
├── s6m-config/                    # 🏯 三省六部部署配置（业务专属）
│   ├── plists/                    # 16 个 launchd plist 副本
│   ├── docs/
│   │   ├── methodology.md         # ADR-001~005
│   │   ├── tracking.md            # 项目追踪 (同步至 Obsidian)
│   │   ├── tdd-plan-review.md     # TDD 计划审查
│   │   ├── tdd-test-plan.md       # TDD 测试计划
│   │   ├── design/                # 设计方案
│   │   │   ├── EmpireThread_事件桥_v2_缩窄版.md
│   │   │   ├── EmpireThread_事件桥_综合设计文档_v1.0.md
│   │   │   ├── Hermes_路线图_v1.0.md
│   │   │   └── ROLEPLAY_cold_start_optimization.md
│   │   ├── optimization/          # 优化方案
│   │   │   ├── s6m-a2a-optimization.md
│   │   │   ├── s6m-a2a-optimization-v2.md
│   │   │   └── resource-optimization-investigation.md
│   │   ├── deployment/            # 部署与调查
│   │   │   ├── deployment-report.md
│   │   │   ├── architecture-comparison.md
│   │   │   └── empirethread-step4-investigation.md
│   │   ├── audits/                # 审计报告
│   │   └── archives/              # 归档数据
│   ├── port-map.md                # 16 profile 端口快查（doctor 读它）
│   ├── discuss-modes.yaml          # 讨论模式配置
│   └── README.md                  # 三省六部部署文档
│
├── README.md                      # 顶层入口（解释 core/ vs s6m-config/）
└── CLAUDE.md                      # 本文件
```

**拆分原则**：
- `core/` 不含任何 profile 名 / 端口号 / 部门名 —— 任何 Hermes 用户都能直接用
- `s6m-config/` 包含一切跟具体治理体系绑定的东西（plist、port-map、ADR、审计）
- 上游协议演进（core）和下游部署演进（s6m-config）可分离节奏

## 开发工作流

```
编辑 ~/code/hermes-a2a/core/  →  cp 到 ~/.hermes/plugins/hermes-a2a/  →  launchctl bootout + bootstrap  →  curl 测试
```

关键命令：
```bash
# 健康聚合（默认读 s6m-config/port-map.md）
bash core/scripts/hermes-a2a-doctor.sh

# 单点测试
curl http://127.0.0.1:8939/health  # regent
curl http://127.0.0.1:8939/a2a/.well-known/agent-card.json | jq

# 同步 core/ 到部署
cp core/*.py core/plugin.yaml ~/.hermes/plugins/hermes-a2a/

# 重启某 profile 的 A2A（plist 改完之后）
HOME=/Users/alexcai launchctl bootout gui/501/com.hermes.a2a.regent
HOME=/Users/alexcai launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.hermes.a2a.regent.plist
```

## 关键约束

1. **HOME hack**：Hermes session 改了 HOME，`launchctl` 必须前置 `HOME=/Users/alexcai` 才能找到 `~/Library/LaunchAgents/`
2. **每次改 core 都要同步**：源码在 `~/code/hermes-a2a/core/`，部署在 `~/.hermes/plugins/hermes-a2a/`，**两边必须保持一致** —— 修部署不同步源码是 P0 错误（参考 audits/02-reaudit.md NEW-P0-A）
3. **CC Agent Team**：大型任务（方法论、架构审查、跨模块重构）拉 CC agent team
4. **Obsidian 同步**：`s6m-config/docs/tracking.md` 和 `methodology.md` 同步到 Obsidian `20-Areas/10_AI实践/三省六部_Hermes/20_实施/追踪/` 和 `20_实施/方法/`
5. **本地测试先行**：改代码后先 curl 验证，再 commit；改 plist 后必须 bootout + bootstrap + curl
6. **Conventional Commits**：`feat:` / `fix:` / `docs:` / `refactor:`

## 部署计划

- Step 1 ✅ 完成：6 profile（engineer/shangshu/budget/regent/default/gongbu）
- Step 2 ✅ 完成：全 16 profile + PORT_RANGE=300 公式化 + launchd 统一监管 + monorepo 拆分
- 讨论模式 ✅ 完成：ROLEPLAY（双边辩论）+ SYNTHESIZE（综合研判），core/discuss.py + a2a-discussion skill
- Step 3 ✅ 完成：EmpireThread 事件桥设计（CC 3-Agent 评估 + 5 加固项）
- Step 4 待启动：EmpireThread 事件桥实施（3 周 ~550 行）

详细端口表见 `s6m-config/port-map.md`。

## 关联系统

- **Default Hermes** — 主频道 / 私人助理 — `localhost:8642` (API Server), `localhost:8945` (A2A)
- **Regent (Crown Prince)** — 监国太子 — `localhost:8643` (API Server), `localhost:8939` (A2A)
- **内阁群** — Telegram 群聊 — `chat_id: -5133970461`
- **Obsidian** — 知识库 — `/Users/alexcai/Documents/Obsidian/AlexCai/`
- **jz-skills** — 技能仓库 — `~/code/jz-skills/`
