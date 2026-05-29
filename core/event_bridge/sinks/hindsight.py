"""Hindsight Sink — REST PUT /memories + 4 层降级.

L0 实时 (2s 超时)
L1 重试 1/4/16s
L2 DLQ
L3 熔断 60s

设计约束:
- write(evt) 只入 pending 队列，不触网（保证 consume_for 非阻塞）
- flush_pending() 由 daemon 独立调用，遍历 pending，按 next_retry_at 调度
- transport 与 clock 都可注入，便于单测
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
import json as _json
from typing import Callable, Optional

from ..core import Event, Sink
from ..dlq import DLQ
from ..paths import event_bridge_home
from ..pending import PendingQueue


class TransportError(Exception):
    """任何 Hindsight 投递失败统一抛此异常."""


# ── 默认 HTTP Transport ──────────────────────────────────────

class HttpTransport:
    def __init__(self, url: str, api_key: str, timeout: float = 2.0):
        self.url = url.rstrip("/") + "/memories"
        self.api_key = api_key
        self.timeout = timeout

    def put_memory(self, payload: dict) -> dict:
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return _json.loads(raw) if raw else {"ok": True}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TransportError(str(e)) from e


# ── HindsightSink ─────────────────────────────────────────────

class HindsightSink(Sink):
    name = "hindsight"

    def __init__(self, *,
                 transport=None,
                 clock: Callable[[], float] = time.time,
                 retry_delays: tuple[float, ...] = (1.0, 4.0, 16.0),
                 max_attempts: int = 3,
                 circuit_threshold: int = 5,
                 circuit_cooldown: float = 60.0,
                 bank_id: Optional[str] = None,
                 home_dir=None):
        self._clock = clock
        self._retry_delays = retry_delays
        self._max_attempts = max_attempts
        self._circuit_threshold = circuit_threshold
        self._circuit_cooldown = circuit_cooldown
        self._bank_id = bank_id or os.environ.get("HINDSIGHT_BANK_ID", "hermes")
        home = home_dir or (event_bridge_home() / "hindsight")
        self._pending = PendingQueue(home / "pending.jsonl")
        self._dlq = DLQ(home / "dlq.jsonl")
        self._transport = transport or self._build_default_transport()
        self._consecutive_failures = 0
        self._breaker_until = 0.0

    @staticmethod
    def _build_default_transport():
        url = os.environ.get("HINDSIGHT_API_URL",
                             "https://api.hindsight.vectorize.io")
        key = os.environ.get("HINDSIGHT_API_KEY", "")
        return HttpTransport(url=url, api_key=key)

    # ── Sink 接口：仅入队 ────────────────────────────────────

    def write(self, evt: Event) -> None:
        payload = self._payload(evt)
        self._pending.enqueue({
            "event": payload,
            "attempts": 0,
            "next_retry_at": self._clock(),
        })

    def _payload(self, evt: Event) -> dict:
        return {
            "bank_id": self._bank_id,
            "event_id": evt.event_id,
            "event_type": evt.event_type,
            "profile": evt.profile,
            "timestamp": evt.timestamp,
            "task_id": evt.task_id,
            "content": evt.content,
        }

    # ── flush_pending（daemon 调用） ───────────────────────────

    def flush_pending(self) -> int:
        """处理 pending 队列：成功推进 cursor、失败重试、用尽进 DLQ.

        Returns: 本次成功投递数.
        """
        now = self._clock()
        if now < self._breaker_until:
            return 0  # L3 熔断期内 no-op

        sent = 0
        items = list(self._pending.iter_pending())
        if not items:
            return 0

        # 当前实现：按顺序处理，依次推进 cursor
        # 若中间某项失败但要保留在 pending 内 → 该项之后的项也不能 advance
        last_advanced_lineno = self._pending._read_cursor()[0]
        rewrite_tail: list[dict] = []

        for it in items:
            entry = it.item
            if entry.get("next_retry_at", 0) > now:
                # 还没到重试时间，整个 pending 后续都保留原样
                rewrite_tail.append(entry)
                continue

            payload = entry["event"]
            try:
                self._transport.put_memory(payload)
            except TransportError:
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                self._consecutive_failures += 1
                if entry["attempts"] >= self._max_attempts:
                    self._dlq.put({
                        "event": payload,
                        "reason": "max_attempts_exhausted",
                        "attempts": entry["attempts"],
                    })
                    # 此项不进 rewrite_tail，即从 pending 中"消费"掉
                    last_advanced_lineno = it.line_no
                else:
                    delay_idx = min(entry["attempts"] - 1,
                                    len(self._retry_delays) - 1)
                    entry["next_retry_at"] = now + self._retry_delays[delay_idx]
                    rewrite_tail.append(entry)
                if self._consecutive_failures >= self._circuit_threshold:
                    self._breaker_until = now + self._circuit_cooldown
            else:
                self._consecutive_failures = 0
                last_advanced_lineno = it.line_no
                sent += 1

        # 重写 pending：把已 DLQ 的从队列剥离，retry 的保留并更新 next_retry_at
        self._rewrite_pending(last_advanced_lineno, rewrite_tail)
        return sent

    def _rewrite_pending(self, advanced_lineno: int,
                         remaining: list[dict]) -> None:
        """删除已 DLQ 的项 + 更新 retry 项的 next_retry_at."""
        # 简单做法：清空文件并重写未消费 + 未 DLQ 的
        path = self._pending.path
        cursor_path = self._pending.cursor_path
        tmp = path.with_suffix(path.suffix + ".rewrite")
        with open(tmp, "wb") as f:
            for entry in remaining:
                line = _json.dumps(entry, ensure_ascii=False) + "\n"
                f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # cursor 归零（文件已被重写，全部待重试项从头开始）
        if cursor_path.exists():
            cursor_path.unlink()

    # ── 状态查询 ───────────────────────────────────────────────

    def is_broken(self) -> bool:
        return self._clock() < self._breaker_until

    def consecutive_failures(self) -> int:
        return self._consecutive_failures
