"""Shared pytest fixtures and sys.path setup for hermes-a2a tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import pytest  # noqa: E402


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME under tmp_path."""
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
    """Reset auth module token cache between tests."""
    import auth
    auth._active_token = None
    yield
    auth._active_token = None
