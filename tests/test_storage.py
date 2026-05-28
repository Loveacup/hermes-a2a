"""Tests for core/storage.py — SQLite-backed TaskStore."""
import threading
from datetime import datetime, timedelta, timezone


def _make_store(tmp_path, **kw):
    from storage import TaskStore
    return TaskStore(tmp_path / "a2a-test.db", **kw)


def test_save_and_get_roundtrip(tmp_path):
    s = _make_store(tmp_path)
    task = {
        "id": "a2a-001", "status": "working", "context_id": "ctx-1",
        "message": {"role": "user", "parts": [{"type": "text", "text": "你好"}]},
        "history": [], "created_at": "2026-05-28T00:00:00+00:00",
    }
    s.save(task)
    got = s.get("a2a-001")
    assert got["id"] == "a2a-001"
    assert got["message"]["parts"][0]["text"] == "你好"
    assert got["context_id"] == "ctx-1"
    s.close()


def test_save_upsert_updates(tmp_path):
    s = _make_store(tmp_path)
    s.save({"id": "a2a-002", "status": "working", "message": {"text": "hi"}})
    s.save({"id": "a2a-002", "status": "completed", "message": {"text": "hi"},
            "artifact": {"response": "done"}, "semantic_status": "succeeded"})
    got = s.get("a2a-002")
    assert got["status"] == "completed"
    assert got["artifact"]["response"] == "done"
    assert got["semantic_status"] == "succeeded"
    s.close()


def test_get_missing_returns_none(tmp_path):
    s = _make_store(tmp_path)
    assert s.get("does-not-exist") is None
    s.close()


def test_list_newest_first_with_status_filter(tmp_path):
    s = _make_store(tmp_path)
    for i in range(3):
        s.save({"id": f"a2a-{i:03d}", "status": "completed" if i == 1 else "working",
                "message": {"text": f"m{i}"}, "created_at": f"2026-05-28T00:00:0{i}+00:00"})
    rows = s.list(limit=10)
    assert [r["id"] for r in rows] == ["a2a-002", "a2a-001", "a2a-000"]
    only_completed = s.list(status="completed")
    assert len(only_completed) == 1 and only_completed[0]["id"] == "a2a-001"
    s.close()


def test_delete(tmp_path):
    s = _make_store(tmp_path)
    s.save({"id": "a2a-x", "status": "working", "message": {"text": "x"}})
    assert s.delete("a2a-x") is True
    assert s.delete("a2a-x") is False
    assert s.get("a2a-x") is None
    s.close()


def test_prune_respects_max_tasks(tmp_path):
    # ttl=0 disables TTL pruning, so we only test the max_tasks cap.
    s = _make_store(tmp_path, max_tasks=3, ttl_seconds=0)
    now = datetime.now(timezone.utc)
    for i in range(5):
        ts = (now + timedelta(seconds=i)).isoformat()
        s.save({"id": f"a2a-{i:03d}", "status": "working",
                "message": {"text": str(i)}, "created_at": ts})
    deleted = s.prune()
    assert deleted >= 2
    remaining = [r["id"] for r in s.list(limit=10)]
    assert len(remaining) <= 3
    assert "a2a-004" in remaining  # newest must survive
    s.close()


def test_prune_respects_ttl(tmp_path):
    s = _make_store(tmp_path, max_tasks=1000, ttl_seconds=1)
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    s.save({"id": "old", "status": "working", "message": {"text": "x"}, "created_at": old})
    s.save({"id": "new", "status": "working", "message": {"text": "y"}})
    s.prune()
    assert s.get("old") is None
    assert s.get("new") is not None
    s.close()


def test_thread_safety(tmp_path):
    s = _make_store(tmp_path)
    errors: list = []

    def writer(prefix: str, n: int):
        try:
            for i in range(n):
                s.save({"id": f"{prefix}-{i:04d}", "status": "working",
                        "message": {"text": f"{prefix}{i}"}})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(f"t{i}", 50)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    rows = s.list(limit=1000)
    assert len(rows) == 200
    s.close()
