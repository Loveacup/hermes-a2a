# ROLEPLAY 冷启动优化 PRD

| Field | Value |
|---|---|
| 版本 | v0.1 (Draft) |
| 创建日期 | 2026-05-30 |
| 作者 | 监国 + Claude |
| 状态 | 设计中（不动生产代码） |
| 关联代码 | `core/discuss.py`、`core/task_handler.py`、`core/skill_resolver.py`、`core/identity.py` |
| 关联 skill | `~/.hermes/profiles/regent/skills/a2a-discussion/` |

---

## 1. 背景与问题陈述

### 1.1 ROLEPLAY 模式现状

`A2ADiscussion.roleplay(topic, rounds=N)` 单次调用承诺：N 轮太子↔小黄双边辩论。当 `N=3` 时，端到端经验耗时 **45–90 s**（依据 discuss.log 实测样本与 SKILL.md "每轮 13–20 次 API 调用、$0.05-0.10/轮" 描述推算）。

冷启动开销集中在 **R1**：第一轮内含 TG 投递、A2A 任务派发、子进程拉起、skill 解析与文件 I/O；后续轮次复用进程 warm path 但每轮仍重复其中数项。

### 1.2 瓶颈感知

通过审计 `core/discuss.py:_a2a_send` ↔ `core/task_handler.py:_via_subprocess`/`_via_api_server` 调用链，识别以下高重复成本路径：

| # | 热点 | 位置 | 单次成本 | 触发频次 |
|---|------|------|---------|---------|
| B1 | `hermes chat -q` 子进程冷启 | task_handler.py:394 | 3–5 s | 非 regent/default profile 每轮 1× |
| B2 | `hermes send` TG 投递子进程冷启 | discuss.py:_tg_send | 3–5 s | 每轮 1× |
| B3 | `identity_prefix` 每任务读盘 | task_handler.py:266 | <50 ms | 每任务 1× |
| B4 | `skill_resolver` 四层目录扫描 | task_handler.py:_resolve_skill_env | 50–200 ms | 每任务 1× |
| B5 | M2CL symlink 检查与创建 | task_handler.py:_ensure_m2cl_symlinks | <50 ms | 每任务遍历 resolved skills |
| B6 | `.env` 逐行解析 | task_handler.py:399-406 | <30 ms | 每任务 1× |
| B7 | ROLEPLAY a2a_prompt 体积膨胀 | discuss.py:497-505 | 影响 LLM 推理 token | 每轮 1× |
| B8 | `comment_kind_backfill` post-tick sweep | task_handler.py:280 | 100 行 SQL × 1 conn | 每任务 1× |
| B9 | 初始 ROLEPLAY prompt 拼装 | discuss.py:471-479 | <5 ms | 每讨论 1× |
| B10 | A2A poll 固定 `A2A_POLL_INTERVAL=2s` 间隔 | discuss.py:52 | tail latency 浪费 | 每轮 N polls |

### 1.3 目标

- **R1 端到端**: ≤ 20 s（当前 ≈ 30–40 s，下降 ≥ 40%）
- **R2-RN 平均轮耗**: ≤ 15 s（当前 ≈ 18–25 s，下降 ≥ 25%）
- **零产品行为退化**: 输出文本质量、TG 投递可见性、风格纪律不可下降
- **不破环 W4 swarm wrapper / orchestrator_router 收敛协议**: 与本周完成的 swarm_wrapper 拓扑层完全正交

### 1.4 非目标

- 不涉及 LLM 自身推理加速（DeepSeek / 模型选型属另一议题）
- 不修改 Hermes 内核（保持 monorepo 拆分原则：`core/` 不含 profile/端口业务）
- 不涉及 SYNTHESIZE / COMBINED / AUTO 模式（虽部分优化项天然外溢，但 PRD 聚焦 ROLEPLAY）

---

## 2. 瓶颈深度剖析

### 2.1 子进程冷启（B1 / B2）

**现象**：`_via_subprocess` 在 task_handler.py:394 执行 `subprocess.run([hermes, "chat", "-q", prompt, "--quiet", "--profile", X, "--skills", "..."])`。每次都从零拉起 Python 解释器 + venv + Hermes agent loop init。

**实测推理**：Hermes CLI 自启动到第一次 LLM 调用，包含：
- Python 解释器启动（~150 ms）
- venv site-packages 加载（~500 ms — pyyaml、anthropic、aiohttp 等）
- Agent 框架初始化（~1.5 s — 配置加载、profile 文件读取、skill 索引）
- Skill 加载（~500 ms — per-task --skills 解析与挂载）
- LLM client 初始化（~300 ms — 鉴权、模型描述符）

**已知缓解路径**：`_API_SERVER_PORTS = {"regent": 8643, "default": 8642}` 已让两个核心 profile 走 API Server 模式（`_via_api_server` POST `/v1/runs`），绕开 subprocess 拉起。**但 ROLEPLAY 的对端是「default」(小黄)，理论上应走 API Server 模式 — 实际是否走该路径取决于 default 的 A2A handler 是否正确读到 `HERMES_PROFILE=default`**。

**未覆盖路径**：若 ROLEPLAY 落到非 regent/default 的 profile（如 hanlinyuan 介入研判），仍走 subprocess。后续若 swarm wrapper 把更多 profile 拉进 ROLEPLAY 风格的轮转辩论，B1 影响面会扩大。

**B2（TG 投递）** 是 _tg_send 调用 `hermes -p regent send -t telegram:...`，同样的冷启路径。每轮 ROLEPLAY 至少 1 次（regent 投递），degraded 时再 +1（regent relay default text）。三轮 ≈ 9–15 s 累计冷启。

### 2.2 Skill 解析（B4 / B5）

`skill_resolver._detect_jz_root` → `Path.is_dir` 探测 → `_resolve_skill_env` 解析每个 `--skill` 名走 dept-self / dept-other / shared / hermes 四层。每次任务都重做。对于 ROLEPLAY 这种"每轮 prompt + skills 不变"的场景，**完全冗余**。

`_ensure_m2cl_symlinks` 遍历 resolved skills，逐个 `target.is_symlink() or target.exists()` 检查。idempotent 是好的，但每任务的 N×stat 调用浪费。

### 2.3 Prompt 体积（B7）

ROLEPLAY 每轮 a2a_prompt 结构：

```
{_ROLEPLAY_PROMPT_TMPL = ~700 字含 stance_clause + style_guide}
+ "=== 朝议历史 ===" + history[-8:] (8 × ~300 字 = ~2400 字)
+ "=== 太子最新发言 (R{r}/{rounds}) ===" + regent_msg (~200-400 字)
+ "请遵【内阁讨论纪律】回奏太子..." (~80 字)
= 3000–4000 字 ≈ 4500–6000 tokens（中文 token 比偏高）
```

**重复内容**：`_ROLEPLAY_PROMPT_TMPL`（含 _DISCUSSION_STYLE_GUIDE）每轮原样重发 ~700 字，而它在小黄 Agent 内是不变的。若小黄 Agent 是无状态接收（A2A 本质上是 stateless task），无法避免；但若小黄 API Server 走 `/v1/runs` 维护了 run 上下文，**首轮注入一次即可，后续轮次仅传增量**。需验证 Hermes API Server `/v1/runs` 是否支持 conversation continuity。

### 2.4 Poll 节奏（B10）

`A2A_POLL_INTERVAL=2` 固定 2 秒间隔。任务实际完成时间通常落在 0.5–1.5 s 内（DeepSeek 短回复）或 8–15 s（深度回复），所以：
- 短回复：平均空等 1 s（首轮 poll 一定打不中）
- 长回复：tail latency 受 2s 离散化影响 ~1 s 浮动

指数退避或自适应间隔（1s → 2s → 3s → 5s）可减少 25–40% tail。

---

## 3. 优化方案

### 3.1 总体路线（三阶段）

**阶段 A — 进程复用**（高 ROI，低风险）
- A1 默认强制 default profile 走 API Server 模式（已有，验证生效）
- A2 TG 投递从 `hermes send` 子进程改为 Hermes API Server `/v1/messages` 或保留 Telegram Bot API 直发

**阶段 B — 缓存与预热**（中 ROI，中风险）
- B1 `identity_prefix` / `skill_resolver` 增加 task_handler 模块级 LRU 缓存（按 profile + sorted(--skills) 元组键）
- B2 `.env` 解析使用 `functools.lru_cache` 装饰的 helper（带 mtime 校验）
- B3 ROLEPLAY 在调用前预热小黄 A2A worker（空任务唤醒 / health probe）

**阶段 C — 协议精简**（中 ROI，需协议变更）
- C1 ROLEPLAY a2a_prompt 抽出 `roleplay_sys` 头部，首轮全注入，后续轮次走 `system_ref_id` 指针
- C2 A2A `/a2a/tasks` 自适应 poll（1s → 2s → 4s 指数）
- C3 `history_window` 从 8 缩到 4，并改用"上一轮全文 + 更早摘要"格式（摘要由 regent 在 prompt 拼装阶段做）

### 3.2 优化项明细

#### O1: 强制 default 走 API Server（阶段 A）

**问题**：B1。验证 ROLEPLAY 调用时小黄是否真的走 `_via_api_server`。

**改动点**：`task_handler.py:270` `if profile in _API_SERVER_PORTS:` 已有路由。需要审计：
1. `_API_SERVER_PORTS` 字典是否在所有部署节点同步（cp 一致性）
2. `HERMES_PROFILE` 是否在 A2A worker 子进程中正确设为 "default"
3. API Server 是否常驻可用（默认 8642）

**预期收益**：每轮 -3 到 -5 s（subprocess 冷启省掉）。3 轮 ROLEPLAY 节省 ≈ 9–15 s。

**风险**：API Server 不可达时自动 fallback 到 subprocess（task_handler.py:302 已有），不会断流。

**验证方式**：
- 在 R1 启动前 `curl 127.0.0.1:8642/health`
- ROLEPLAY 期间 `tail -f ~/.hermes/logs/discuss.log` 看 `mode=api_server` 还是 `mode=subprocess`

#### O2: TG 投递改 Bot API 直发（阶段 A）

**问题**：B2。`_tg_send` 每次拉起 hermes CLI 仅为发一条 TG 消息。

**改动点**：新增 `discuss.py:_tg_send_direct(text, chat_id)` 用 `urllib.request` 直接打 Telegram Bot API：

```python
def _tg_send_direct(self, text: str, chat_id: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN_REGENT")  # 复用现有 .env
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except (urllib.error.URLError, OSError) as e:
        logger.warning(f"direct TG send failed, falling back to hermes CLI: {e}")
        return self._tg_send(text, chat_id)  # 保留旧路径作 fallback
```

**预期收益**：每次 TG 投递 -3 到 -5 s。3 轮 ROLEPLAY 节省 ≈ 9–15 s（regent 端）。

**风险**：
- Telegram Bot Token 暴露面扩大 — 必须确认 `.env` 中 token 是 regent 专属
- Hermes 的发送审计日志不会经过 → 需在 `~/.hermes/logs/discuss.log` 单独记录
- 失败时 fallback 到 hermes CLI 保持兼容

**前置依赖**：审计 `.env` 中 TG bot token 命名与权限范围。

#### O3: skill_env 模块级 LRU 缓存（阶段 B）

**问题**：B3/B4/B5。

**改动点**：`task_handler.py` 顶部增加：

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def _cached_skill_env(profile: str, skill_key: str) -> tuple[dict, tuple]:
    """skill_key = ",".join(sorted(per_task_skills))."""
    # 同 _resolve_skill_env，但去掉每次调用的 jz_root 探测
    ...
```

`_resolve_skill_env` 改为薄包装，从 task 提取 per-task skills → 排序 → 查缓存。

**预期收益**：每任务 -50 到 -200 ms。3 轮 ROLEPLAY 节省 ≈ 150–600 ms。

**风险**：
- skill_resolver 输出包含 Path 对象，缓存可能 stale（skill 文件被改了不会感知）
- 缓解：缓存 key 加入 jz_root 的 mtime 戳；或部署时显式 invalidate

#### O4: identity_prefix 缓存（阶段 B）

**问题**：B3。

**改动点**：`identity.py:load_identity_prefix` 已是单行读盘，加 `@lru_cache(maxsize=32)` 即可。Key = (hermes_home, profile)。

**预期收益**：每任务 -10 到 -50 ms。

**风险**：开发期修改 identity 文件不立即生效 → 加一个 `_identity_cache_invalidate()` 调试钩子。

#### O5: ROLEPLAY 预热（阶段 B）

**问题**：R1 的额外冷启来源 — 小黄 worker 进程可能彻底未拉起（API Server 在但 default 的 LLM client 是 lazy 初始化）。

**改动点**：`A2ADiscussion.roleplay` 入口处：

```python
# 预热小黄 — 一次 GET /health 或最小 task
if not self.dry_run:
    self._prewarm_default()
```

`_prewarm_default` 内容：`GET http://127.0.0.1:8642/v1/healthz` 已存在（验证），无额外开销。

**预期收益**：R1 -2 到 -5 s（LLM client 在 health probe 触发下提前 warm）。R2+ 基本无影响。

**风险**：零。即使 prewarm 失败，正常路径不变。

#### O6: 自适应 A2A poll（阶段 C）

**问题**：B10。

**改动点**：`discuss.py:_a2a_poll`：

```python
def _a2a_poll(self, tid, max_wait=None):
    ...
    intervals = iter([1, 1, 2, 2, 3, 3, 5, 5, 5, ...])  # 平均更早打中
    while time.time() - start < wait:
        time.sleep(next(intervals, 5))
        ...
```

**预期收益**：每轮 tail latency -0.5 到 -1.5 s。3 轮节省 ≈ 1.5–4.5 s。

**风险**：高频 poll 增加 A2A server 负载。建议封顶到 5s/poll 后稳定，且 server 端 HTTP 是 O(1) read，影响可忽略。

#### O7: history_window 由 8 改 4 + 早期轮次摘要（阶段 C）

**问题**：B7。

**改动点**：`discuss.py:DEFAULT_HISTORY_WINDOW=4`；在 R≥3 时，对超出窗口的早期轮次做"3 句压缩"，由 regent 端组装 prompt 时本地完成（不再 LLM 调用）。

**预期收益**：a2a_prompt -800 到 -1200 字 ≈ -1000 tokens。DeepSeek 推理 -0.5 到 -1.5 s/轮。3 轮节省 ≈ 1.5–4.5 s。

**风险**：上下文窗口收紧可能影响小黄的呼应能力。缓解：保留近 4 条 + 早期摘要 + 议题原文。

#### O8: discussion_style_guide 模块级常量传递（阶段 C）

**问题**：B7 的延伸。`_DISCUSSION_STYLE_GUIDE` 当前每轮原样塞入 prompt（700+ 字）。

**改动点**：若小黄 Agent 端支持 system_prompt 持续记忆（需验证 API Server `/v1/runs` 文档），首轮 init 注入一次，后续 prompt 引用 `[style:cabinet-v1]` 标签即可。否则不动。

**预期收益**：若支持，每轮 -700 字 ≈ -1000 tokens；3 轮节省 ≈ 3–6 s。
**风险**：需协议变更，依赖 Hermes API Server 能力。

#### O9: ROLEPLAY 前置健康检查门禁化（阶段 A）

**问题**：当前 SKILL.md 描述了三级自检，但是手动的。若小黄不可达，ROLEPLAY 第一轮才发现，浪费 R1 cold start。

**改动点**：`roleplay()` 入口：

```python
if not self.dry_run and not self._healthcheck_quick():
    raise ConnectionError("小黄 A2A unreachable — 预检失败，请先 hermes gateway restart")
```

`_healthcheck_quick` = 单次 `GET /health` + 100ms timeout。

**预期收益**：失败时省去 R1 的 60s 超时。
**风险**：零（仅在失败时短路）。

---

## 4. 优化收益汇总

| 阶段 | 优化项 | R1 节省 | R2+ 节省/轮 | 实现复杂度 |
|------|--------|---------|------------|-----------|
| A | O1 API Server 路由验证 | -3 to -5 s | -3 to -5 s | 低（审计） |
| A | O2 TG Bot API 直发 | -3 to -5 s | -3 to -5 s | 中（新增 helper） |
| A | O9 预检门禁 | -60 s (失败时) | — | 低 |
| B | O3 skill_env LRU | -100 ms | -100 ms | 低 |
| B | O4 identity LRU | -30 ms | -30 ms | 极低 |
| B | O5 小黄预热 | -2 to -5 s | — | 低 |
| C | O6 自适应 poll | -0.5 to -1.5 s | -0.5 to -1.5 s | 低 |
| C | O7 history_window=4 | -0.5 to -1.5 s | -0.5 to -1.5 s | 中（摘要逻辑） |
| C | O8 style_guide 持久注入 | -1 to -2 s (若可) | -1 to -2 s | 高（协议变更） |

**估计最终态**（全 9 项落地）：
- R1: 30–40 s → **18–22 s**（满足 ≤20s 目标）
- R2-RN 均值: 18–25 s → **12–17 s**（满足 ≤15s 目标）
- 3 轮 ROLEPLAY 总耗时: 90 s → **~50 s**

---

## 5. 实施路线

### 5.1 W5（next week）— 阶段 A 落地

| 序号 | 工时 | 任务 |
|------|------|------|
| W5-1 | 0.5 h | 审计 `_API_SERVER_PORTS` 在所有部署节点一致；验证 `HERMES_PROFILE` 注入正确（O1） |
| W5-2 | 2 h | 实现 `_tg_send_direct` + 旧路径 fallback；audit `.env` token 范围（O2） |
| W5-3 | 0.5 h | 在 `roleplay()` 入口加 quick healthcheck（O9） |
| W5-4 | 1 h | 写 `test_discuss_optimizations.py` 单测覆盖 fallback 路径 |

### 5.2 W6 — 阶段 B 落地

| 序号 | 工时 | 任务 |
|------|------|------|
| W6-1 | 1 h | 加 `_cached_skill_env` LRU（O3） |
| W6-2 | 0.5 h | `load_identity_prefix` 加 `@lru_cache`（O4） |
| W6-3 | 0.5 h | 在 `roleplay()` 入口加 `_prewarm_default`（O5） |
| W6-4 | 1 h | 用 monkeypatch 写缓存命中/失效测试 |

### 5.3 W7 — 阶段 C 评估与可选落地

| 序号 | 工时 | 任务 |
|------|------|------|
| W7-1 | 1 h | 实现 `_a2a_poll` 自适应间隔（O6） |
| W7-2 | 2 h | `DEFAULT_HISTORY_WINDOW=4` + 摘要逻辑（O7）|
| W7-3 | 0.5 h | 调研 Hermes API Server 是否支持 system_prompt 持久化（O8 决策点） |
| W7-4 | 2 h | 端到端 benchmark：旧路径 vs 新路径 × {dry-run, live} × N=3 |

---

## 6. 风险与回退

### 6.1 关键风险

| ID | 风险 | 严重度 | 缓解 |
|----|------|--------|------|
| R1 | TG Bot API 直发导致 hermes 审计断流 | 中 | discuss.log 单独记录；degraded 时仍走 hermes CLI |
| R2 | LRU 缓存让开发期修改 identity / skill 不生效 | 低 | 提供 `--clear-cache` CLI 标志；或基于 mtime 失效 |
| R3 | history_window=4 导致小黄上下文丢失 | 中 | 保留议题原文 + 早期摘要兜底；A/B 测试观察输出质量 |
| R4 | 自适应 poll 在 LLM 极慢响应时过早超时 | 低 | 总 `a2a_timeout=300s` 不变，只动间隔节奏 |
| R5 | 与 W4 swarm_wrapper、本周 convergence_check 路径冲突 | 低 | 优化点全在 transport 层（discuss.py / task_handler.py），与 orchestrator_router 正交；regression test 把关 |

### 6.2 回退策略

每个优化项独立可关：
- O1/O5/O9 可通过 env `DISCUSS_FAST_PATH=0` 一键关
- O2 fallback 自动到 hermes CLI
- O3/O4 缓存可 monkey-patch 清空
- O6/O7 改回原 constant 值即可

---

## 7. 验证计划

### 7.1 单元测试（伴随实现）

- `test_tg_send_direct_uses_bot_api`
- `test_tg_send_direct_falls_back_on_failure`
- `test_skill_env_cache_hit`
- `test_identity_prefix_cache_invalidation_on_mtime_change`
- `test_a2a_poll_uses_adaptive_intervals`
- `test_history_window_truncation_keeps_topic`

### 7.2 集成测试

- ROLEPLAY 3 轮 dry-run，对比 prompt 体积 before/after
- ROLEPLAY 3 轮 live（在 default 端开 trace），对比每轮 wall clock

### 7.3 退化检测

- 输出质量：抽 10 个历史议题，用 W5 之前的 ROLEPLAY 与 W7 之后的对比输出风格、立场、长度，由监国人工评分
- TG 投递可见性：投递成功率必须 ≥99%（与现状持平）
- 审计完整性：每条 TG 投递必须有 `discuss.log` 记录

---

## 8. 决策点 / 待确认

| ID | 问题 | 待答 |
|----|------|------|
| D1 | Hermes API Server `/v1/runs` 是否支持 system_prompt 持久注入（决定 O8 可行性）？ | 监国调研 |
| D2 | TG Bot Token 范围 — 是否一个 token 服务多 chat？ | 审计 `.env` |
| D3 | `history_window=4` 是否会显著影响 R3 之后小黄对早期立场的呼应？ | A/B 测试 |
| D4 | 是否在阶段 C 引入 prompt 模板版本号（便于 A/B）？ | 设计期决定 |

---

## 9. 附录

### A. 相关代码路径

- `core/discuss.py:147-172` — `_ROLEPLAY_PROMPT_TMPL`
- `core/discuss.py:428-557` — `A2ADiscussion.roleplay`
- `core/discuss.py:387-425` — `_tg_send`
- `core/task_handler.py:251-281` — `handle_task`
- `core/task_handler.py:284-340` — `_via_api_server`
- `core/task_handler.py:391-428` — `_via_subprocess`
- `core/task_handler.py:157+` — `_resolve_skill_env`
- `core/skill_resolver.py:38-65` — `_detect_jz_root`
- `core/identity.py` — `load_identity_prefix`

### B. SKILL.md 相关章节

- `~/.hermes/profiles/regent/skills/a2a-discussion/SKILL.md` §「自检流程」
- 同上 §「ROLEPLAY」
- 同上 §「降级与容错」
- `references/p1-04-send-message-credentials.md` — TG 发送排障

### C. 关联设计

- `s6m-config/docs/s6m-a2a-optimization-v2.md` — A2A 优化总览（与本 PRD 互补，本 PRD 聚焦 ROLEPLAY）
- `s6m-config/docs/EmpireThread_事件桥_v2_缩窄版.md` — W3 narrowed 设计
- W4 swarm_wrapper（已落地）—— 拓扑层，与本 PRD 的 transport 层正交

---

**End of v0.1**
