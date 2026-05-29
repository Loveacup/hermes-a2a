"""launchd sidecar 入口.

KeepAlive=true，每 N 秒一个 tick，每次发现并 dispatch 全 profile JSONL.
W2 后接入 fsevents/kqueue 加速；W1 用纯轮询.
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import Iterable

from .core import Sink, dispatch_all
from .paths import jsonl_paths_for_all_profiles
from .sinks.obsidian import ObsidianSink

log = logging.getLogger("event_bridge.daemon")


def default_sinks() -> list[Sink]:
    return [ObsidianSink()]


def tick(sinks: Iterable[Sink]) -> dict[str, int]:
    return dispatch_all(sinks, jsonl_paths_for_all_profiles())


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
