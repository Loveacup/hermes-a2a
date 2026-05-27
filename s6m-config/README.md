# hermes-a2a/s6m-config — 三省六部部署配置

`core/` 是通用 A2A 协议内核；本目录是**三省六部治理体系**专属的部署配置 + 设计文档 + 审计记录。

## 目录结构

```
s6m-config/
├── plists/                          # 16 个 launchd plist 实物副本
│   └── com.hermes.a2a.<profile>.plist × 16
├── docs/
│   ├── methodology.md               # ADR 方法论文档（ADR-001 ~ ADR-004）
│   ├── tracking.md                  # 项目追踪日志（与 Obsidian 同步）
│   ├── architecture-comparison.md   # A2A vs API Bridge 对比分析
│   ├── deployment-report.md         # 接入 regent + default 部署报告
│   ├── s6m-a2a-optimization.md      # 三省六部 A2A 体系优化方案
│   └── audits/                      # CC agent team 审计报告
│       ├── 01-initial-audit.md      # 首次 P0×3 / P1×6 / P2×6 审计
│       ├── 02-reaudit.md            # 二轮再审计（综合）
│       └── 02-reaudit-ops-agent.md  # 二轮 ops 子 agent 完整 trace
├── port-map.md                      # 16 profile 端口映射（doctor.sh 读它）
└── README.md                        # 本文件
```

## 当前部署清单

16 个 profile 全部由 launchd 监管（KeepAlive=true, ThrottleInterval=30），端口公式 `sha256(profile) % 300 + 8650`，零碰撞。详见 `port-map.md`。

## 部署流程

### 首次部署（从零搭起 16 profile A2A）

1. 把 `core/` symlink 或 cp 到 `~/.hermes/plugins/hermes-a2a/`：
   ```bash
   ln -s ~/code/hermes-a2a/core ~/.hermes/plugins/hermes-a2a
   ```
2. 用 `core/scripts/seed-a2a-symlinks.sh` 为每个 profile 建 per-profile 插件 symlink
3. 把本目录 `plists/` 下 16 个 plist 拷到 `~/Library/LaunchAgents/`：
   ```bash
   cp s6m-config/plists/com.hermes.a2a.*.plist ~/Library/LaunchAgents/
   ```
4. 逐个 bootstrap（注意 HOME 必须显式设到真用户家）：
   ```bash
   for f in ~/Library/LaunchAgents/com.hermes.a2a.*.plist; do
     HOME=/Users/<your-username> launchctl bootstrap gui/$(id -u) "$f"
   done
   ```
5. 验证：`bash core/scripts/hermes-a2a-doctor.sh` 应显示 16/16 ALL HEALTHY

### 增量部署新 profile

1. 在 `port-map.md` 加一行
2. 用 `core/templates/a2a-launchd.plist` 模板替换 `{{PROFILE}}` / `{{PORT}}` / `{{HERMES_HOME}}`
3. 落盘到 `plists/com.hermes.a2a.<name>.plist` + `~/Library/LaunchAgents/`
4. bootstrap + 跑 doctor 验证

### 端口迁移（如果端口公式参数改了）

1. 重新算端口落进 `port-map.md`
2. 对应改 `plists/` 下 plist 的 `A2A_PORT` 字段
3. 逐个 `launchctl bootout` → `launchctl bootstrap`
4. 用 doctor.sh 验证全部跑在新端口上

## 三省六部 16 profile 速查（端口由小到大）

- `:8642` default API Server （Hermes 原生，非 A2A）
- `:8643` regent API Server （Hermes 原生）
- `:8654` jiangzuojian 将作监
- `:8698` auditor 御史中丞
- `:8702` hanlinyuan 翰林院
- `:8707` dispatcher 派工调度器
- `:8718` engineer 兵部
- `:8728` planner 策划
- `:8755` tester 测试员
- `:8761` reviewer 御史台
- `:8804` archivist 史馆
- `:8826` shangshu 尚书省
- `:8833` protocol 礼部
- `:8898` gongbu 工部
- `:8928` registry 注册管理
- `:8936` budget 户部
- `:8939` regent 监国太子
- `:8945` default 主频道 Hermes

## 关键约束（实际部署踩坑后总结）

- **HOME hack**：Hermes session 把 HOME 改成 profile 沙箱，因此 `launchctl` 命令必须前置 `HOME=/Users/<real-user>`，否则 plist 找不到 `~/Library/LaunchAgents/` 真路径
- **symlink 必需**：Hermes 在非 default profile 下扫 `~/.hermes/profiles/<name>/plugins/` 而非全局 `~/.hermes/plugins/`，所以每个 profile 都得有 symlink 指向插件源
- **default 特殊**：default profile 用 `~/.hermes/` 本身作为 HERMES_HOME，不在 `~/.hermes/profiles/default/` 下
- **plists 是副本不是源**：本目录的 plists 用于版本管理；实际生效的是 `~/Library/LaunchAgents/` 下的拷贝。更新流程必须两边同步

## 设计与决策

- **完整 ADR**：见 `docs/methodology.md`（ADR-001 ~ ADR-004 含 A2A 选择、symlink 部署、HTTP/JSON 先行、自动 Agent Card 生成）
- **A2A vs API Bridge**：见 `docs/architecture-comparison.md`（两套协议的字段映射、融合路线图）
- **三省六部体系优化方案**：见 `docs/s6m-a2a-optimization.md`（dispatcher / 御史台 / hanlinyuan 三大支柱）

## 审计历史

- **01-initial-audit.md**：首轮 P0×3（端口碰撞、task_handler 未调用、无看门狗）/ P1×6 / P2×6
- **02-reaudit.md**：二轮综合报告——P0-1 仍部分破（PORT_RANGE=200 还差最后一步到 300）、P0-2/P0-3 已修；新发现 P0：源码↔部署↔git 三处不同步
- **02-reaudit-ops-agent.md**：ops 子 agent 完整 trace（launchd 行为、健康聚合输出、子进程恢复实测）
