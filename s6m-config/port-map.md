---
tags: [hermes-s6m-a2a, 三省六部, 端口]
---

# 三省六部 A2A 端口映射表

端口公式：`8650 + sha256(profile) % 300` (PORT_RANGE=300)

零碰撞验证通过：16 个 profile 全部分配到唯一端口。

## 16 Profile 部署清单

- **archivist** (史馆) — 端口 `8804` — skills: 2
- **auditor** (御史中丞 / 复审员) — 端口 `8698` — skills: 3
- **budget** (户部) — 端口 `8936` — skills: 3
- **default** (主频道 Hermes) — 端口 `8945` — skills: 13（fallback to _BASE_TOOLSETS）
- **dispatcher** (派工调度器) — 端口 `8707` — skills: 4
- **engineer** (兵部) — 端口 `8718` — skills: 5
- **gongbu** (工部) — 端口 `8898` — skills: 4
- **hanlinyuan** (翰林院 / 知识库) — 端口 `8702` — skills: 6
- **jiangzuojian** (将作监 / 校验门闸) — 端口 `8654` — skills: 5
- **planner** (策划) — 端口 `8728` — skills: 2
- **protocol** (礼部 / 协议管理) — 端口 `8833` — skills: 3
- **regent** (监国太子) — 端口 `8939` — skills: 13（fallback to _BASE_TOOLSETS）
- **registry** (注册管理) — 端口 `8928` — skills: 3
- **reviewer** (御史台) — 端口 `8761` — skills: 2
- **shangshu** (尚书省) — 端口 `8826` — skills: 4
- **tester** (测试员) — 端口 `8755` — skills: 4

## 端口范围
- 最低：8654 (jiangzuojian)
- 最高：8945 (default)
- 总跨度：291 端口（在 PORT_RANGE=300 内）

## 双 API Server（Hermes 原生，与 A2A 共存）
- **default API Server** — 端口 `8642`
- **regent API Server** — 端口 `8643`

## 端口冲突防御
- A2A 与 API Server 完全分离端口空间（A2A 8654-8945，API Server 8642-8643）
- 16 profile A2A 之间数学验证无碰撞（PORT_RANGE=300 实测）
- 跨进程方案：sha256 取代 Python `hash()`，跨 launchd 重启端口稳定

## 用法
- `core/scripts/hermes-a2a-doctor.sh` 默认读本文件
- 手动覆盖：`PORT_MAP=/path/to/custom.md bash hermes-a2a-doctor.sh`
- 或参数：`bash hermes-a2a-doctor.sh --port-map /path/to/custom.md`

## 解析格式说明
- 本文件可被脚本解析：每行格式 `- **<profile>** ... 端口 \`<port>\` ...`
- 解析正则：`^- \*\*([a-z_]+)\*\*.*端口 \`(\d+)\``
- 注释行（任何不匹配该正则的行）忽略
