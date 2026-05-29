"""EmpireThread v2 事件桥 — Obsidian + Hindsight 2-Sink dispatcher.

设计文档: s6m-config/docs/EmpireThread_事件桥_v2_缩窄版.md
"""
from .core import Event, Sink, consume_for, dispatch_all
from .cursor import Cursor, CursorStore

__all__ = ["Event", "Sink", "Cursor", "CursorStore",
           "consume_for", "dispatch_all"]
