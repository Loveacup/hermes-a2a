"""Cross-profile A2A runtime registry.

Single source of truth for which A2A server.py instance owns which (profile,
port, pid) tuple at runtime. Lives at ``~/.hermes/a2a-registry.json`` so all
profiles share it (writes serialised by fcntl flock).

Solves:
    BUG-006 — plugin.py reads this on startup to kill stale server.py owned
              by the same profile, preventing zombie accumulation across
              gateway restarts.
    BUG-008 — discovery clients read this to find every live profile's port
              without `ps aux | grep` scans.

Schema::

    {
        "<profile>": {
            "pid": int,
            "host": str,
            "port": int,
            "started_at": "<iso8601 UTC>",
            "version": str
        },
        ...
    }

Stale-entry semantics:
    A registry entry is *stale* if its ``pid`` is no longer alive OR the
    process at that pid is not a server.py (was recycled). Callers must
    treat stale entries as absent. ``cleanup_stale()`` prunes them in bulk.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes-a2a.registry")

_REGISTRY_FILENAME = "a2a-registry.json"
_LOCK_FILENAME = "a2a-registry.lock"
_PROC_MATCH = "hermes-a2a/server.py"  # substring expected in /proc/<pid>/cmdline equivalent


try:
    from . import paths as _paths  # package context
except (ImportError, ValueError):
    import paths as _paths  # flat-deploy context (sys.path injected by server.py)


def _hermes_root() -> Path:
    """Shared registry dir, with ``HERMES_REGISTRY_DIR`` as a test escape hatch."""
    override = os.environ.get("HERMES_REGISTRY_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return _paths.hermes_root()


def registry_path() -> Path:
    return _hermes_root() / _REGISTRY_FILENAME


def _lock_path() -> Path:
    return _hermes_root() / _LOCK_FILENAME


class _Lock:
    """Context manager for an exclusive fcntl lock on the registry file."""

    def __init__(self) -> None:
        self._fd: int | None = None

    def __enter__(self) -> "_Lock":
        lock = _lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        # O_CREAT so concurrent first-callers don't race on file creation.
        self._fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _read_unlocked() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("registry: read failed (%s)", e)
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("registry: corrupt JSON (%s); ignoring", e)
        return {}
    return data if isinstance(data, dict) else {}


def _write_unlocked(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.EPERM  # exists but we lack perms
    return True


def _pid_is_server(pid: int) -> bool:
    """Best-effort check that ``pid`` is a hermes-a2a server.py.

    On macOS /proc is unavailable; fall back to ``ps -p <pid> -o command=``.
    Returns True if the command line contains _PROC_MATCH, False otherwise
    (including on lookup failure — fail closed to avoid killing unrelated
    processes).
    """
    if pid <= 0:
        return False
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    if out.returncode != 0:
        return False
    return _PROC_MATCH in out.stdout


def get(profile: str) -> dict[str, Any] | None:
    """Return the registry entry for ``profile`` (or None)."""
    with _Lock():
        return _read_unlocked().get(profile)


def get_all() -> dict[str, dict[str, Any]]:
    """Return a snapshot of *live* registry entries (stale entries pruned)."""
    with _Lock():
        data = _read_unlocked()
        live: dict[str, dict[str, Any]] = {}
        dirty = False
        for profile, entry in list(data.items()):
            pid = int(entry.get("pid", 0))
            if _pid_alive(pid) and _pid_is_server(pid):
                live[profile] = entry
            else:
                dirty = True
        if dirty:
            _write_unlocked(live)
    return live


def upsert(profile: str, *, host: str, port: int, pid: int, version: str = "") -> None:
    """Write/overwrite the entry for ``profile``."""
    entry = {
        "pid": int(pid),
        "host": host,
        "port": int(port),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
    }
    with _Lock():
        data = _read_unlocked()
        data[profile] = entry
        _write_unlocked(data)
    logger.info("registry: upsert profile=%s pid=%d port=%d", profile, pid, port)


def remove(profile: str, expected_pid: int | None = None) -> None:
    """Remove the entry for ``profile``.

    If ``expected_pid`` is given and the stored entry's pid differs, this is
    a no-op — we don't clobber a successor's registration with our shutdown
    cleanup. This protects against the race where:

        old server (pid=A) writes entry → restarts → new server (pid=B)
        writes entry → old server's atexit fires → would erase B's entry.
    """
    with _Lock():
        data = _read_unlocked()
        existing = data.get(profile)
        if not existing:
            return
        if expected_pid is not None and int(existing.get("pid", 0)) != int(expected_pid):
            logger.info(
                "registry: skip remove profile=%s (pid=%s != expected=%s)",
                profile, existing.get("pid"), expected_pid,
            )
            return
        data.pop(profile, None)
        _write_unlocked(data)
    logger.info("registry: remove profile=%s pid=%s", profile, expected_pid)


def kill_stale(profile: str, *, term_grace: float = 3.0,
               skip_pid: int | None = None) -> int:
    """Kill any prior server.py owned by ``profile``.

    Returns the number of processes killed (0 if nothing to do). Intended for
    plugin.py to call *before* spawning a new server.py, and for server.py
    itself to call *before* binding the port (covering both launch paths).

    Algorithm:
        1. Read registry entry for profile.
        2. If pid == skip_pid → no-op (avoid suicide).
        3. If pid alive AND command line matches server.py:
             SIGTERM → wait up to term_grace → SIGKILL if still alive.
        4. Remove the entry (fresh server will write a new one).

    Fail-safe: any error short-circuits to logging + return 0; we never raise
    from this path because it sits on the gateway boot critical path.
    """
    killed = 0
    try:
        with _Lock():
            data = _read_unlocked()
            entry = data.get(profile)
            if not entry:
                return 0
            pid = int(entry.get("pid", 0))
            if skip_pid is not None and pid == int(skip_pid):
                return 0
            if not _pid_alive(pid):
                data.pop(profile, None)
                _write_unlocked(data)
                return 0
            if not _pid_is_server(pid):
                logger.info(
                    "registry: pid=%d for profile=%s not a server.py; clearing entry",
                    pid, profile,
                )
                data.pop(profile, None)
                _write_unlocked(data)
                return 0

            logger.info(
                "registry: killing stale server.py profile=%s pid=%d port=%s",
                profile, pid, entry.get("port"),
            )
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                logger.warning("registry: SIGTERM pid=%d failed: %s", pid, e)
                data.pop(profile, None)
                _write_unlocked(data)
                return 0

            deadline = time.monotonic() + term_grace
            while time.monotonic() < deadline:
                if not _pid_alive(pid):
                    killed = 1
                    break
                time.sleep(0.1)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                    killed = 1
                    logger.warning("registry: SIGKILL pid=%d after %ss grace", pid, term_grace)
                except OSError as e:
                    logger.warning("registry: SIGKILL pid=%d failed: %s", pid, e)

            data.pop(profile, None)
            _write_unlocked(data)
    except OSError as e:
        logger.warning("registry: kill_stale failed for profile=%s: %s", profile, e)
    return killed


def cleanup_stale() -> int:
    """Bulk-prune every dead entry from the registry. Returns count removed."""
    removed = 0
    with _Lock():
        data = _read_unlocked()
        for profile, entry in list(data.items()):
            pid = int(entry.get("pid", 0))
            if not (_pid_alive(pid) and _pid_is_server(pid)):
                data.pop(profile, None)
                removed += 1
        if removed:
            _write_unlocked(data)
    return removed


__all__ = [
    "registry_path",
    "get",
    "get_all",
    "upsert",
    "remove",
    "kill_stale",
    "cleanup_stale",
]


def _cli(argv: list[str]) -> int:
    """Minimal CLI for ad-hoc discovery / cleanup.

    Usage:
        python registry.py                 # JSON dump of live entries
        python registry.py get <profile>   # JSON for one profile (or exit 1)
        python registry.py port <profile>  # bare port number (or exit 1)
        python registry.py cleanup         # prune stale entries; print count
        python registry.py path            # absolute registry file path
    """
    if len(argv) == 1:
        print(json.dumps(get_all(), indent=2, ensure_ascii=False))
        return 0
    cmd = argv[1]
    if cmd == "path":
        print(registry_path())
        return 0
    if cmd == "cleanup":
        print(cleanup_stale())
        return 0
    if cmd == "get" and len(argv) >= 3:
        entry = get(argv[2])
        if not entry:
            print("not found", file=__import__("sys").stderr)
            return 1
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0
    if cmd == "port" and len(argv) >= 3:
        entry = get(argv[2])
        if not entry:
            print("not found", file=__import__("sys").stderr)
            return 1
        print(entry["port"])
        return 0
    print(_cli.__doc__, file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv))
