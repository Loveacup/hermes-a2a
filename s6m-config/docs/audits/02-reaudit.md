# hermes-a2a 第二轮审计报告

## 综合评分一句话
- 上一轮 P0×3 / P1×6 / P2×6 大多确实修了，但 **P0-1 端口碰撞修不彻底**（PORT_RANGE=200 仍有 1 处碰撞），且 **修复未回写源码 / 未提交 git / 未推 GitHub**——这是这次审计最关键的新发现，作为本轮新 P0 单列

## 阻断项（new P0 from this round）
- **NEW-P0-A：源码 ↔ 部署副本不同步，修复未持久化**
  - 实测 `diff -r ~/code/hermes-a2a/ ~/.hermes/plugins/hermes-a2a/` 显示 4 个 .py 文件全部不一致
  - 部署副本：`plugin.py: PORT_RANGE = 200` + `server.py: threading.Thread(target=_execute_task)` + `agent_card.py: 完整 SKILL_MAP dict-of-dicts + _BASE_TOOLSETS 兜底` + `task_handler.py: 双模实现`
  - 源码 `~/code/hermes-a2a/`：`plugin.py: PORT_RANGE = 50` + server.py / agent_card.py / task_handler.py 全是修复前的旧版
  - git status：`docs/methodology.md modified`（ADR-002 改了但没提交）；`scripts/` 整个目录在源码里不存在
  - GitHub origin/main 头部还在 `95ce18d`（5月27日，本次修复前）
  - 后果：任何 clone 自 GitHub 的人拿到的是坏代码；本地 `~/.hermes/plugins/` 一旦被覆盖/误删，所有 P0/P1/P2 修复瞬间蒸发
  - 修复：把 4 个 .py + plugin.yaml + docs/methodology.md + 新建 scripts/ 都同步到 ~/code/hermes-a2a/，commit + push

- **NEW-P0-B：PORT_RANGE=200 仍有 1 处碰撞，P0-1 修复不彻底**
  - 实测对 16 个 unique profile（archivist / auditor / budget / default / dispatcher / engineer / gongbu / hanlinyuan / jiangzuojian / planner / protocol / regent / registry / reviewer / shangshu / tester）跑 `sha256(p) % 200 + 8650`，**planner 和 registry 都落到 :8828**
  - 当前 6 个 launchd-managed 服务躲过去，因为它们 plists 写死的还是上一轮 PORT_RANGE=50 时代的端口（8668/8676/8686/8689/8695/8698）；plugin.py 的新公式对它们是死代码
  - 但下一步部署 planner 或 registry（哪怕只有 1 个，再加另一个时）会直接 EADDRINUSE
  - 实测 PORT_RANGE=300 完全无碰撞（已验证 16 profiles 全独立）
  - 修复：PORT_RANGE 200 → 300（一行 + 同步源码 + 重写 plugin.yaml 描述里的 "% 50" / "% 200" 字符串）

## 上轮 P0/P1/P2 逐项判定

### ✅ P0-2 task_handler 与 server.py 接通 — FIXED
- 部署 `server.py:74` 确认有 `threading.Thread(target=_execute_task, args=(tid,), daemon=True).start()`
- `server.py:89-115` 的 `_execute_task` 调 `handle_task(task)` 然后 `_tasks[tid] = result`，状态会从 "working" → "completed/failed"
- **独立活检（我直接 curl 验证）**：
  - regent :8689 POST `{"id":"sanity-regent",...}` → 20s 后 GET 返回 `status=completed`, `artifact.mode=api_server`, `artifact.duration_s=4.52`, `artifact.response=AUDIT_PONG_REGENT`
  - default :8695 POST `{"id":"sanity-default",...}` → 25s 后 GET 返回 `status=completed`, `artifact.mode=api_server`, `artifact.duration_s=4.49`, `artifact.response=AUDIT_PONG_DEFAULT`
- Agent 1 进一步在 engineer :8668 跑了 subprocess 模式的 task，10.54s 完成，`artifact.mode=subprocess`
- 三个模式都跑通，task 真的会执行而不是停留在 "working"

### ✅ P0-3 launchd 看门狗 — FIXED
- 全部 6 个 plist 存在于 `/Users/alexcai/Library/LaunchAgents/com.hermes.a2a.{engineer,shangshu,budget,gongbu,regent,default}.plist`
- 每个 plist 含 `KeepAlive=true` + `ThrottleInterval=30` + `RunAtLoad=true` + 完整 EnvironmentVariables (HERMES_PROFILE / A2A_PORT / HERMES_HOME / PATH)
- `launchctl list | grep hermes-a2a` 显示 6 个服务均在管理之下，PID 与 lsof LISTEN 进程对应
- **实测**：Agent 1 杀 engineer :8668 进程后 ~1 秒内 launchd 自动拉起新 PID（agent 报告 1s，与之前上一轮 default :8695 的 1s 实测一致）
- 旧的 gateway-spawned 路径现在等于死代码——4 个 worker gateway 还活着但子 A2A 进程都死了无人复活；所有现在能 ping 的 A2A 都是 launchd 拉的

### ⚠️ P0-1 端口碰撞 — PARTIAL（见 NEW-P0-B 的详细说明）
- 已从 PORT_RANGE=50 升到 200，三处碰撞减一到 1 处
- 但 planner ↔ registry 仍在 :8828 撞车（独立计算确认）
- 修复方案明确：再升到 300，验证零碰撞

### ✅ P1-1 server.py 异常处理 — FIXED
- 部署 `server.py:28-44` 的 `_read_body` 现在：
  - Content-Length 字符串先 try `int()`，非法返回 400
  - 长度范围检查 `0 <= length <= 1_000_000`，否则 413
  - `json.loads` 包 try/except 返回 400
- `do_POST` 收到 `_read_body()` 返回 None 时直接 return（错误已发）

### ✅ P1-2 Agent Card schema 跨实例一致 — FIXED
- 6 个 A2A 服务现在都跑同一个最新部署版的 server.py + agent_card.py
- 因为统一走 launchd 重启 → `_BASE_TOOLSETS` 兜底 + 新 SKILL_MAP 全部生效
- 实测（curl /agent-card 计 skills 数）：
  - engineer :8668 → 5 skills [shell-execution, file-operations, web-research, code-execution, health-check]
  - shangshu :8676 → 4 skills [shell-execution, file-operations, kanban-workflow, health-check]
  - budget :8686 → 3 skills [file-operations, web-research, health-check]
  - gongbu :8698 → 4 skills [shell-execution, file-operations, web-research, health-check]
  - regent :8689 → 13 skills（_BASE_TOOLSETS 全集，因为 config 只有 hermes-cli）
  - default :8695 → 13 skills（同上）
- 不再有上一轮的 "13 vs 3-5 split"

### ✅ P1-3 `_BASE_TOOLSETS` 撒谎 — FIXED
- `agent_card.py:34` `toolsets = config_toolsets if config_toolsets else _BASE_TOOLSETS`
- 现在的语义：profile config 显式声明 toolsets 就只用 config 的（不撒谎）；config 空才回退到 _BASE_TOOLSETS（regent/default 这种 hermes-cli-only 的情况）
- budget 的 3 skills（file/web/health）就对得上它 config 真实声明的 toolsets
- regent/default 报 13 skills 是诚实的——它们的 agent 走的是 default 实例，能力面广

### ✅ P1-5 plist 缺 ThrottleInterval — FIXED
- 全 6 个 plist 都有 `<key>ThrottleInterval</key><integer>30</integer>`
- 不会重启循环打满 CPU

### ✅ P1-6 缺全局健康聚合器 — FIXED
- `~/.hermes/plugins/hermes-a2a/scripts/hermes-a2a-doctor.sh` 存在且可执行
- 实测输出（agent 1 跑过）：8/8 端点全 200 OK，并附技能数统计

### ✅ P2-1 yaml 回退分支死代码 — FIXED
- `grep -nE "ImportError|yaml_fallback|except ImportError" agent_card.py` → 0 结果
- 直接 `import yaml`，干净

### ✅ P2-2 双模执行 — FIXED
- `task_handler.py:14` 有 `_API_SERVER_PORTS = {"regent": 8643, "default": 8642}`
- `handle_task()` 根据 HERMES_PROFILE 是否在这个映射里二选一
- `_via_api_server` 走 `urllib.request` POST `/v1/runs` + 轮询 `/v1/runs/{run_id}` + 失败自动 fallback 到 subprocess
- `_via_subprocess` 走 `subprocess.run([hermes_bin, "chat", "-q", prompt, "--quiet", "--profile", profile], timeout=300)`
- 两路都在 `artifact.mode` 字段标 `"api_server"` 或 `"subprocess"`，可观测
- HTTP 路径有合理超时（urlopen timeout=10 for create, 5 for poll）
- 还做了贴心事：subprocess 路径会读 `~/.hermes/.env` 注入 API key，避免子进程拿不到鉴权

### ✅ P2-4 SKILL_MAP A2A 1.0 schema — FIXED
- 部署 `agent_card.py:7-20` 把 SKILL_MAP 改成 dict-of-dicts，每条都有 id / name / description / examples / tags 五字段
- 实测 curl shangshu :8676 拿到的 skills[0] 是
  - `{"id":"shell-execution","name":"Shell Execution","description":"Execute shell commands and scripts","examples":["run tests","deploy app","manage processes"],"tags":["cli","automation"]}`
- 完整对齐 A2A spec 1.0 的 AgentSkill schema
- protocolVersion 字段返 "1.0" 正确

### (skipped) P2-5 stream endpoint
- 按 brief 跳过，明确未做

### ✅ P2-6 seed 脚本 + ADR-002 修订 — FIXED（但有 caveat）
- `~/.hermes/plugins/hermes-a2a/scripts/seed-a2a-symlinks.sh` 存在且可执行（1053 字节）
- ~/code/hermes-a2a/docs/methodology.md ADR-002 已改成「共享代码 + per-profile symlink 部署」，状态标 "已修订"，并提到 PORT_RANGE=200 + seed 脚本
- caveat：methodology.md 改动还**没 commit**（仍在 working tree）；ADR-002 里写 "PORT_RANGE=200 解决" 这句不准确（其实还差最后一步到 300）

## 跨协议互通实测（本轮独立验证）
- regent API Bridge (8643) → engineer A2A (8668) /agent-card.json
  - POST `/v1/chat/completions` model=hermes-agent，prompt 让它 terminal curl engineer 的 agent card 并打印 name + skill count
  - 响应 content: `"Hermes Agent — engineer 5 skills"` ✅
- 上一轮死过的 default API Bridge (8642) 这次活着：curl localhost:8642/health → `{"status":"ok"}`
- 不再有上一轮的「两端都活着才能跑通」可用性问题

## 全 6 端点 /health 实测（一次性扫描）
- engineer :8668 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"engineer"}`
- shangshu :8676 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"shangshu"}`
- budget   :8686 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"budget"}`
- regent   :8689 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"regent"}`
- default  :8695 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"default"}`
- gongbu   :8698 → `{"status":"ok","service":"hermes-a2a","version":"0.1.0","profile":"gongbu"}`
- 全部 200 OK，profile 字段与端口对应

## 进程清单（活检时点）
- launchd-managed A2A（KeepAlive=true，崩溃 ~1s 内自启）：6 个进程，PID 78782/78790/78798/78806/78812/79793（engineer 这条是 agent 1 杀过又新起的，所以 PID 偏大）
- API Server（Hermes 原生）：default :8642 (PID 76623)、regent :8643 (PID 72205)
- 老的 worker gateway（shangshu/engineer/gongbu/budget）4 个 PID 68987/68995/68999/69005 还活着，但**它们 spawn 的 A2A 子进程全死了无人复活**——这是 gateway-spawned 路径的死状态，跟上一轮 P0-3 现象一样；不过现在不影响可用性，因为 launchd 接管了所有真实 A2A 流量

## 端口数学独立复算（PORT_RANGE=200 全 16 profile）
- :8654 jiangzuojian · 未部署
- :8661 reviewer · 未部署
- :8698 auditor · 未部署（如果 launchd plist 写 8698 会和 gongbu 的 plist 端口撞——但 gongbu 历史用的是 PORT_RANGE=50 时代的 8698，**两组算法在 8698 上巧合相同**，所以这是一个 "新公式刚好跟旧公式打架" 的隐藏雷）
- :8704 archivist · 未部署
- :8718 engineer · ⚠️ 新公式应分 8718 但 plist 写的是 8668；不影响运行但代码与部署不一致
- :8726 shangshu · 同上
- :8736 budget · 同上
- :8745 default · 同上
- :8755 tester · 未部署
- :8798 gongbu · 同上
- :8802 hanlinyuan · 未部署
- :8807 dispatcher · 未部署
- **:8828 planner + registry · ★ 碰撞**
- :8833 protocol · 未部署
- :8839 regent · 同上

## 本轮新发现汇总（按优先级）

### 🔴 P0（必修）
- **NEW-P0-A**：源码 ↔ 部署 ↔ GitHub 三处不同步，修复未持久化（见顶部）
- **NEW-P0-B**：PORT_RANGE=200 仍存在 planner/registry 碰撞，需升到 300（见顶部）

### 🟠 P1（应修）
- **NEW-P1-A**：plugin.py 的 PORT_RANGE 公式与 launchd plist 的硬编码 A2A_PORT 之间存在分歧
  - 6 个现役 plist 写的是 PORT_RANGE=50 时代的端口（8668/8676/...）
  - 新代码 PORT_RANGE=200 算出来的是另一组（8718/8726/...）
  - 现在没冲突是因为 launchd 的 plist 端口优先（server.py 读 A2A_PORT env 直接用）
  - 但「源代码描述的端口」和「实际跑的端口」是两套，添 profile 时算法和现实会脱节
  - 修复建议：把端口算法从 plugin.py 抽到一个统一脚本（如 seed-a2a-plist.sh），生成 plist 时调用，部署/绘文档/写 README 都从这一个真源读
- **NEW-P1-B**：methodology.md ADR-002 已修订但没 commit；也没记录 PORT_RANGE 还要再升一次到 300
- **NEW-P1-C**：plugin.yaml 描述字段仍写 "sha256(profile) % 50 + 8650"，没跟代码同步到 % 200（更不用说 % 300）

### 🟡 P2（可缓）
- **NEW-P2-A**：worker 4 个 gateway 还在跑但 A2A 子进程已死，等于内存占着、CPU 偶尔被 gateway 心跳吃，且会误导新人以为 "gateway-spawned 路径还能工作"。建议要么停掉这 4 个 gateway，要么真的把 worker gateway 的 plugin path 也修好（加 watchdog 或直接删 register 的 Popen 调用）
- **NEW-P2-B**：`_via_api_server` 在 polling 时遇到不能解的 response 用 `continue` 静默吞掉，永远不退出 loop 直到 300s timeout——如果 /v1/runs/{run_id} 接口返回非预期 schema 会僵住整个 task。建议改成 "连续 N 次解析失败就 fail"
- **NEW-P2-C**：scripts/ 整个目录（doctor + seed）只在部署副本里，源码 ~/code/hermes-a2a/ 没有。需要 git add 进去

## 修复路线建议（按工时）
- 5 分钟：PORT_RANGE 200 → 300，sed 一行；同时改 plugin.yaml 描述字符串到 "% 300 + 8650"
- 5 分钟：把部署副本 4 个 .py 文件 + plugin.yaml + scripts/ 拷回 ~/code/hermes-a2a/
- 5 分钟：`git add -A && git commit && git push origin main`，把所有修复 + ADR-002 修订一起入仓
- 15 分钟：写一个 `scripts/generate-plist.sh`，参数化 PROFILE，输出对应的 plist 到 ~/Library/LaunchAgents/，端口走统一 sha256 % 300 公式，部署新 profile 一行调用
- 10 分钟：worker 4 个 gateway 现状决定：停掉（`hermes -p X gateway stop`）让 launchd 独家管 A2A；或留着但删 plugin.py 里的 Popen 让 worker 不再 spawn 重复 server.py

## 整体评估
- 上轮 P0×3 都有进展：P0-2 / P0-3 完全 fixed；P0-1 partial（还差最后一步从 200 升到 300）
- 上轮 P1×6 全部 fixed；P2×6 除了明确跳过的 P2-5 之外全部 fixed
- 本轮新发现的 NEW-P0-A（持久化 gap）比任何一个老 P0 都重要——它意味着所有现在看似已修的东西都是「单机本地状态」，没有任何 git/远程持久化兜底
- 距离生产就绪：需要把 NEW-P0-A（push 到 git）+ NEW-P0-B（端口公式再升一次）解掉，再做一次端到端冒烟，可以认为 v0.1.1 OK

## Agent 团队产出归档
- Agent 1 (ops/resilience)：`/tmp/reaudit-ops.md`（631 行，A 评级，所有上轮 P 项判 FIXED）；漏掉的点——只测了当前在跑的 6 个 profile 的端口，没用算法对全 16 profile 复算，所以错过了 planner+registry 碰撞
- Agent 2 (code-quality)：未落盘文件，但完整摘要在主 agent 上下文里；**关键贡献——抓出 PORT_RANGE=200 仍碰撞 + 源码 deployed 不一致 + plugin.yaml 描述滞后**，是本轮唯一发现 NEW-P0 的 agent
- Agent 3 (schema/correctness)：在主 agent 已经写完报告之后才返回（耗时 442 秒）；评 9.5/10、判全部 FIXED——同样只看当前 6 个 profile 的端口，把端口公式与 plist 硬编码不一致仅标 P2 "doc inconsistency"；漏掉了 planner+registry 碰撞和源码持久化 gap
- **主 agent 综合判定以独立活检 + Agent 2 发现为准**（Agent 1 / 3 的 "all FIXED" 是测试样本偏差导致的乐观判定）
