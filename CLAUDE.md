# CLAUDE.md — hermes-a2a

## 项目身份

**hermes-a2a** 是 Hermes Agent 的全局 A2A（Agent-to-Agent）协议插件，为三省六部（15 profile）提供标准化跨部门通信。

- 仓库：`~/code/hermes-a2a/`
- 插件：`~/.hermes/plugins/hermes-a2a/`
- 协议：Google A2A Protocol (a2aproject/A2A, 23.4K⭐, Apache 2.0)

## 三省六部背景

本项目是三省六部治理体系的通信基础设施。当前 15 个 profile（中书/门下/尚书/兵部/工部/户部/礼部/刑部/吏部/御史台/史馆/将作监/翰林院/dispatcher/tester）之间仅靠 Kanban 卡异步通信，本项目引入 A2A 协议实现同步、实时的跨 profile 能力发现与任务委派。

## 架构

```
hermes-a2a/
├── plugin.py            # Hermes 插件入口（gateway 启动时自动加载）
├── server.py            # A2A HTTP Server（Agent Card + Task 端点）
├── agent_card.py        # 从 profile config 自动生成 Agent Card
├── task_handler.py      # A2A Task → Hermes agent loop 转发
├── requirements.txt     # pyyaml
├── docs/
│   ├── tracking.md      # 项目追踪（与 Obsidian 同步）
│   └── methodology.md   # 方法论文档
├── CLAUDE.md            # 本文件
└── README.md            # 用户文档
```

## 开发工作流

```
编辑 ~/code/hermes-a2a/  →  cp 到 ~/.hermes/plugins/hermes-a2a/  →  gateway restart  →  curl 测试
```

关键命令：
```bash
# 测试 server
python3 ~/.hermes/plugins/hermes-a2a/server.py &
curl localhost:8650/health
curl localhost:8650/a2a/.well-known/agent-card.json | jq

# 部署到插件目录
cp ~/code/hermes-a2a/*.py ~/.hermes/plugins/hermes-a2a/

# 启用插件
hermes plugins enable hermes-a2a --profile shangshu
```

## 关键约束

1. **主频道协作**：重大决策前通过 API (localhost:8642) 与 default Hermes 讨论，群聊内走内阁合议模式
2. **CC Agent Team**：大型任务（方法论文档、架构审查、跨模块重构）拉 CC agent team 做重活
3. **Obsidian 同步**：`docs/tracking.md` 和 `docs/methodology.md` 的更新同步到 Obsidian `00-Inbox/`
4. **GitHub 同步**：每次提交前确认 Obsidian 文档已更新
5. **本地测试先行**：修改代码后先 curl 验证，再 commit
6. **Conventional Commits**：`feat:` / `fix:` / `docs:` / `refactor:`

## 测试

```bash
# 健康检查
curl localhost:8650/health

# Agent Card
curl localhost:8650/a2a/.well-known/agent-card.json | python3 -m json.tool

# 创建 Task
curl -X POST localhost:8650/a2a/tasks \
  -H "Content-Type: application/json" \
  -d '{"message": {"text": "test task"}}'

# 查询 Task
curl localhost:8650/a2a/tasks/{task_id}

# SSE 流
curl localhost:8650/a2a/tasks/{task_id}/stream
```

## 部署计划

| 阶段 | 范围 | 端口分配 |
|------|------|---------|
| Step 1 | shangshu+engineer+gongbu+budget | 8650-8653 |
| Step 2 | 全 15 profile | 8650-8664 |
| Step 3 | EmpireThread 事件桥 | MEMORY_QUERY → Hindsight |

## 关联系统

| 系统 | 角色 | 端点 |
|------|------|------|
| Default Hermes | 主频道 / 私人助理 | localhost:8642 |
| Regent (Crown Prince) | 监国太子 | localhost:8643 |
| 内阁群 | Telegram 群聊 | chat_id: -5133970461 |
| Obsidian | 知识库 | /Users/alexcai/Documents/Obsidian/AlexCai/ |
| jz-skills | 技能仓库 | ~/code/jz-skills/ |
