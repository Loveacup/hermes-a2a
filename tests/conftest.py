"""Shared pytest fixtures for hermes-a2a tests.

Layout:
    - Legacy fixtures (hermes_home, reset_auth) — kept for existing test_auth.py / test_storage.py
    - P0 fixtures (tmp_hermes_home, kanban_db, dispatcher_daemon, ...) — per tdd-test-plan.md v1.1

Reference: s6m-config/docs/tdd-test-plan.md §0.2 / §4.4 of tdd-plan-review.md
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

REAL_HERMES_HOME = Path.home() / ".hermes"
PORT_MAP_PATH = ROOT / "s6m-config" / "port-map.md"
PORT_MAP_RE = re.compile(r"^- \*\*([a-z_]+)\*\*.*端口 `(\d+)`")
MIGRATION_SQL = ROOT / "s6m-config" / "migrations" / "001_a2a_comment_kinds.sql"


# ─────────────────────────────────────────────────────────────
# Legacy fixtures (kept for backward compatibility)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Legacy minimal HERMES_HOME. Used by test_auth.py / test_storage.py."""
    home = tmp_path / "hermes-home"
    (home / "data").mkdir(parents=True)
    (home / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "testprof")
    monkeypatch.setenv("A2A_HOST", "127.0.0.1")
    import auth
    auth._active_token = None
    yield home


@pytest.fixture
def reset_auth():
    import auth
    auth._active_token = None
    yield
    auth._active_token = None


# ─────────────────────────────────────────────────────────────
# P0 fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def hermes_version_assert():
    """Lock hermes to v0.15.x. Bail loudly if upgraded/downgraded."""
    out = subprocess.check_output(["hermes", "--version"], text=True)
    assert "v0.15." in out, (
        f"TDD plan v1.1 targets hermes v0.15.x; got: {out.strip()}"
    )
    return out.strip()


@pytest.fixture(scope="session", autouse=True)
def _ensure_jz_skills_root():
    """Resolve jz-skills root for the whole test session and export to env.

    Under Hermes profile sandbox HOME is hijacked to <profile>/<name>; the
    test's Path.home() then no longer points at the real user home and
    skill_resolver's standard layout misses. Setting JZ_SKILLS_ROOT here
    makes the chain deterministic regardless of HOME state.
    """
    candidates = [
        os.environ.get("JZ_SKILLS_ROOT", "").strip() or None,
        str(Path.home() / "code" / "jz-skills"),
        "/Users/alexcai/code/jz-skills",
    ]
    chosen = next((c for c in candidates if c and Path(c).is_dir()), None)
    if chosen is None:
        pytest.skip("jz-skills repo not found at any known location; "
                    "set JZ_SKILLS_ROOT to enable P0-2 tests")
    os.environ["JZ_SKILLS_ROOT"] = chosen
    yield chosen


@pytest.fixture(scope="session")
def port_pool():
    """Parse port-map.md. Regex is empirically 16/16 hit."""
    text = PORT_MAP_PATH.read_text(encoding="utf-8")
    pool = {
        m.group(1): int(m.group(2))
        for line in text.splitlines()
        for m in [PORT_MAP_RE.match(line)] if m
    }
    assert len(pool) == 16, f"want 16 profiles, got {len(pool)} from {PORT_MAP_PATH}"
    return pool


@pytest.fixture
def tmp_hermes_home(tmp_path_factory, monkeypatch, hermes_version_assert):
    """Isolated HERMES_HOME with 16 profile symlinks.

    Production profiles total ~15 GB (regent alone is 5.4 GB), so we MUST NOT
    copy them. Strategy:
      - tmp HERMES_HOME/profiles/<name> -> symlink to real profile
      - hermes kanban init only reads config.yaml during discovery; it writes
        kanban.db at HERMES_HOME root (NOT inside profiles/), so symlinks are
        read-only enough for unit-level testing.
      - For tests that need to mutate a profile's interior, override this
        fixture and replace the symlink with a copy.

    Tmp prefix kept short ("p") to avoid pytest's 10-tries-per-prefix exhaustion.
    """
    # Use a short numbered dir to dodge long-prefix collisions
    home = tmp_path_factory.mktemp("p", numbered=True)

    profiles_dst = home / "profiles"
    profiles_dst.mkdir()
    src_profiles = REAL_HERMES_HOME / "profiles"
    if src_profiles.exists():
        for prof in src_profiles.iterdir():
            if prof.is_dir():
                (profiles_dst / prof.name).symlink_to(prof, target_is_directory=True)

    # Shared A2A token (small file, safe to copy)
    token_src = REAL_HERMES_HOME / ".a2a-token"
    if token_src.exists():
        shutil.copy(token_src, home / ".a2a-token")

    monkeypatch.setenv("HERMES_HOME", str(home))
    # Isolate HOME-sandbox hack so paths.hermes_root() resolves under tmp
    monkeypatch.setenv("HOME", str(home.parent))

    yield home


def _run_hermes(args, env_extra=None, check=True, capture=True):
    env = os.environ.copy()
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run(
        ["hermes", *args],
        env=env,
        check=check,
        text=True,
        capture_output=capture,
        timeout=120,
    )


@pytest.fixture
def kanban_db(tmp_hermes_home):
    """Run `hermes kanban init` in isolation and return the sqlite path."""
    _run_hermes(["kanban", "init"], env_extra={"HERMES_HOME": tmp_hermes_home})
    db = tmp_hermes_home / "kanban.db"
    assert db.exists(), f"hermes kanban init did not create {db}"
    return db


@pytest.fixture
def kanban_conn(kanban_db):
    """sqlite3.Connection on the isolated kanban.db (read-only-ish; tests may write)."""
    conn = sqlite3.connect(str(kanban_db))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def a2a_migration_applied(kanban_db):
    """Apply 001_a2a_comment_kinds.sql to the isolated kanban_db."""
    assert MIGRATION_SQL.exists(), f"migration missing: {MIGRATION_SQL}"
    sql = MIGRATION_SQL.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(kanban_db))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    return kanban_db


@pytest.fixture
def dispatcher_daemon(tmp_hermes_home, kanban_db):
    """Start `hermes kanban daemon --force` (NOT `hermes gateway start`).

    In v0.15.x the standalone daemon is deprecated in favor of the
    gateway-embedded dispatcher. We use --force here because:
      - `hermes gateway start` would also launch the messaging gateway
        (Telegram/Discord/WhatsApp) and risk sending real messages from CI
      - We only want the dispatcher loop for lifecycle assertions

    `--interval 3600` keeps tick frequency low so the daemon does not
    actually try to spawn worker subprocesses during the short window of
    the test (we use --dry-run-dispatcher fixture for spawn decisions).
    """
    pidfile = tmp_hermes_home / "dispatcher.pid"
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_hermes_home)
    env["HOME"] = str(tmp_hermes_home.parent)
    proc = subprocess.Popen(
        ["hermes", "kanban", "daemon", "--force",
         "--pidfile", str(pidfile),
         "--interval", "3600",
         "--verbose"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Give the daemon up to 8s to write its pidfile
    deadline = time.time() + 8
    while time.time() < deadline and not pidfile.exists() and proc.poll() is None:
        time.sleep(0.1)
    yield {"proc": proc, "pidfile": pidfile}
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def dry_run_dispatcher(tmp_hermes_home, kanban_db):
    """One-shot `hermes kanban dispatch --dry-run --json` decision check."""
    def _call(max_spawn=None):
        args = ["kanban", "dispatch", "--dry-run", "--json"]
        if max_spawn is not None:
            args += ["--max", str(max_spawn)]
        result = _run_hermes(args, env_extra={"HERMES_HOME": tmp_hermes_home})
        return json.loads(result.stdout)
    return _call
