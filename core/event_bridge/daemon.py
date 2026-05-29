"""launchd sidecar 入口.

KeepAlive=true，每 N 秒一个 tick，每次发现并 dispatch 全 profile JSONL.
W2 后接入 fsevents/kqueue 加速；当前用纯轮询.

Tick 内做两件事：
1. dispatch_all: empire-thread.jsonl → sink.write (Obsidian 直写、Hindsight 入 pending)
2. flush_pending: 凡是 Sink 暴露 flush_pending() 都调用一次（Hindsight 的网络投递）
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Iterable

from .core import Sink, dispatch_all
from .paths import jsonl_paths_for_all_profiles
from .sinks.obsidian import ObsidianSink
from .sinks.hindsight import HindsightSink

log = logging.getLogger("event_bridge.daemon")


def default_sinks() -> list[Sink]:
    sinks: list[Sink] = [ObsidianSink()]
    if os.environ.get("HINDSIGHT_API_KEY"):
        sinks.append(HindsightSink())
    else:
        log.info("HINDSIGHT_API_KEY 未设置，跳过 HindsightSink")
    return sinks


def tick(sinks: Iterable[Sink]) -> dict[str, int]:
    sinks_list = list(sinks)
    counts = dispatch_all(sinks_list, jsonl_paths_for_all_profiles())
    for s in sinks_list:
        flush = getattr(s, "flush_pending", None)
        if callable(flush):
            sent = flush()
            if sent:
                counts[f"{s.name}/flush"] = sent
    return counts


def run(poll_interval: float = 1.0) -> None:
    sinks = default_sinks()
    while True:
        counts = tick(sinks)
        if any(counts.values()):
            log.info("dispatch: %s", counts)
        time.sleep(poll_interval)


def main() -> None:
    p = argparse.ArgumentParser(prog="event_bridge.daemon")
    p.add_argument("--interval", type=float, default=1.0,
                   help="poll interval seconds")
    p.add_argument("--once", action="store_true",
                   help="run one tick then exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.once:
        print(tick(default_sinks()))
    else:
        run(args.interval)


if __name__ == "__main__":
    main()
