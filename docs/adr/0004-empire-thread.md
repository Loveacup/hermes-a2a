# ADR-0004：EmpireThread 事件桥与体系拓扑

**日期**：2026-05-28
**状态**：接受
**父 ADR**：ADR-001（选择 A2A 作为跨 profile 通信主干）

## 背景

EmpireThread 是三省六部治理体系的事件溯源层，负责：
- 跨 profile 的事件流标准化（10 种 XML 标签）
- 上下文标签集渲染（context_tags.py → `<system_history>`）
- MEMORY_QUERY 事件桥（A2A Task 承载，Hindsight 只读查询）

随着 hermes-a2a 讨论编排引擎（ROLEPLAY + SYNTHESIZE）的落地，需要明确体系中所有角色的拓扑关系。

## 决策

明确小黄（default profile）的独立地位，在三省六部体系拓扑中标注为独立实体。

## 体系拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    三省六部治理体系                              │
│                                                                 │
│  ┌─────────── 三省 ───────────┐    ┌──── 六部 ────────────────┐ │
│  │  中书省 (planner)          │    │  兵部 (engineer)          │ │
│  │  门下省 (reviewer)         │    │  工部 (gongbu) 🆕        │ │
│  │  尚书省 (shangshu)         │    │  户部 (budget)            │ │
│  └────────────────────────────┘    │  礼部 (protocol)          │ │
│                                    │  刑部 (tester)            │ │
│  ┌──── 扩展部门 ──────────────┐    │  吏部 (registry)          │ │
│  │  御史台 (auditor)          │    └──────────────────────────┘ │
│  │  史馆 (archivist)          │                                  │
│  │  将作监 (jiangzuojian)     │    ┌────────────────────────┐  │
│  │  翰林院 (hanlinyuan)       │    │  监国太子 (regent)     │  │
│  └────────────────────────────┘    │  port 8939 · 总枢      │  │
│                                    │  承旨·拟制·派工·稽核  │  │
│  ┌────────────────────────────┐    └───────────┬────────────┘  │
│  │  小黄 (default)            │                │                │
│  │  port 8945                 │    A2A 平等协作 │                │
│  │  ⚠️ 独立于三省六部之外     │◄───────────────┘                │
│  │  Alex 的个人助理           │                                  │
│  │  不隶属任何三省六部部门    │                                  │
│  └────────────────────────────┘                                  │
│                                                                 │
│  通信层：A2A Protocol (HTTP/JSON) + Kanban (异步审计)           │
│  事件层：EmpireThread (10 种 XML 标签 → context_tags.py)         │
│  讨论层：discuss.py (ROLEPLAY / SYNTHESIZE via A2A)              │
└─────────────────────────────────────────────────────────────────┘
```

## 小黄身份定义

| 属性 | 值 |
|------|-----|
| Profile | `default` |
| A2A 端口 | 8945 |
| API Server | 8642 |
| 身份 | Alex 的个人助理 |
| 与三省六部关系 | **独立于体系之外**，不隶属任何部门 |
| 与太子关系 | **平等协作**，非上下级 |
| A2A 讨论中落款 | 【小黄】 |
| 主要能力 | 个人助理、独立分析、与太子对等讨论 |

## 理由

1. **角色清晰**：小黄不是三省六部的「主频道助手」或「default profile 职员」——他是 Alex 的私人助理，有自己的独立视角
2. **讨论质量**：在内阁群 ROLEPLAY/SYNTHESIZE 中，两个独立视角的碰撞 > 一个体系内的自我对话
3. **A2A 协议层面**：`discuss.py` 的身份声明和 `task_handler.py` 的身份前缀已实现代码层面的独立声明
4. **架构简洁**：不把个人助理塞进治理体系的部门层级，保持三省六部纯粹面向治理任务

## 后果

- 小黄的 profile 配置、skills、memory 独立维护，不与三省六部部门 profile 混合管理
- A2A 讨论的 prompt 中明确身份声明（已实现在 `discuss.py` 和 `task_handler.py` 中）
- 未来 EmpireThread MEMORY_QUERY 事件桥中，小黄可作为独立的 Hindsight bank 查询源
- 内阁群对话中，小黄发言代表个人判断，不代三省六部决策

## 关联文档

- [methodology.md](../methodology.md) — 其他 ADR（ADR-001~004）
- [../s6m-a2a-optimization.md](../s6m-a2a-optimization.md) — 体系优化方案
- [../../README.md](../../README.md) — 顶层 README（体系角色表）
- [CLAUDE.md](../../CLAUDE.md) — AI 协作文档
- Obsidian：`20-Areas/10_AI实践/三省六部_Hermes/10_制度/Agent名册(三省六部制).md`
- Obsidian：`20-Areas/10_AI实践/三省六部_Hermes/10_制度/监国三省六部制Agent架构方案.md`
