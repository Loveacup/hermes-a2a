"""E2E test fixtures — auto-applied to all tests under tests/e2e/."""

import pytest
import sqlite3
import subprocess
import os
from pathlib import Path


ROOT = Path(__file__).parent.parent.parent  # tests/e2e/conftest.py → repo root
MIGRATION_SQL = ROOT / "s6m-config" / "migrations" / "001_a2a_comment_kinds.sql"


@pytest.fixture(autouse=True)
def _ensure_a2a_migration(tmp_hermes_home):
    """Ensure a2a_comment_kinds table exists in the isolated kanban.db.

    E2E tests drive hermes kanban CLI which auto-inits kanban.db on first
    command. We must apply the migration AFTER init but BEFORE any test
    uses the a2a_comment_kinds table. We do this by running kanban init
    ourselves and then applying the migration.

    This fixture is autouse=True so every E2E test gets it transparently.
    """
    assert MIGRATION_SQL.exists(), f"migration missing: {MIGRATION_SQL}"

    db_path = tmp_hermes_home / "kanban.db"

    # Init kanban.db if not already done
    if not db_path.exists():
        env = os.environ.copy()
        env["HERMES_HOME"] = str(tmp_hermes_home)
        subprocess.run(
            ["hermes", "kanban", "init"],
            env=env,
            capture_output=True,
            timeout=30,
        )

    # Apply migration (CREATE TABLE IF NOT EXISTS — idempotent)
    sql = MIGRATION_SQL.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()

    yield
