"""Tests for core/auth.py — token + CORS."""


def test_load_or_create_token_from_env(hermes_home, monkeypatch, reset_auth):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "env-token-xyz")
    import auth
    tok = auth.load_or_create_token(str(hermes_home))
    assert tok == "env-token-xyz"


def test_load_or_create_token_from_file(hermes_home, monkeypatch, reset_auth):
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    # BUG-007: _resolve_token_path() uses hermes_root(), not hermes_home.
    # Set HERMES_ROOT so the file path matches what load_or_create_token reads.
    monkeypatch.setenv("HERMES_ROOT", str(hermes_home))
    (hermes_home / ".a2a-token").write_text("file-token-abc")
    import auth
    tok = auth.load_or_create_token(str(hermes_home))
    assert tok == "file-token-abc"


def test_load_or_create_token_auto_generates(hermes_home, monkeypatch, reset_auth):
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    # BUG-007: token is saved to hermes_root(), not hermes_home.
    monkeypatch.setenv("HERMES_ROOT", str(hermes_home))
    import auth
    tok = auth.load_or_create_token(str(hermes_home))
    assert len(tok) >= 32
    saved = (hermes_home / ".a2a-token").read_text().strip()
    assert saved == tok
    mode = (hermes_home / ".a2a-token").stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_token_persists_across_calls(hermes_home, monkeypatch, reset_auth):
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    import auth
    t1 = auth.load_or_create_token(str(hermes_home))
    auth._active_token = None
    t2 = auth.load_or_create_token(str(hermes_home))
    assert t1 == t2


class _Headers(dict):
    def get(self, k, default=""):
        return super().get(k, default)


def test_check_auth_public_paths(hermes_home, reset_auth):
    import auth
    auth.load_or_create_token(str(hermes_home))
    assert auth.check_auth(_Headers(), "/health")[0]
    assert auth.check_auth(_Headers(), "/a2a/.well-known/agent-card.json")[0]


def test_check_auth_missing_header_rejected(hermes_home, reset_auth):
    import auth
    auth.load_or_create_token(str(hermes_home))
    ok, reason = auth.check_auth(_Headers(), "/a2a/tasks")
    assert not ok
    assert "Authorization" in reason


def test_check_auth_invalid_token(hermes_home, reset_auth):
    import auth
    auth.load_or_create_token(str(hermes_home))
    ok, reason = auth.check_auth(_Headers({"Authorization": "Bearer wrong"}), "/a2a/tasks")
    assert not ok
    assert "invalid" in reason


def test_check_auth_valid_token(hermes_home, monkeypatch, reset_auth):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "good-token")
    import auth
    auth.load_or_create_token(str(hermes_home))
    ok, _ = auth.check_auth(_Headers({"Authorization": "Bearer good-token"}), "/a2a/tasks")
    assert ok


def test_cors_allows_default_origins(monkeypatch):
    monkeypatch.delenv("A2A_CORS_ORIGINS", raising=False)
    import auth
    h = auth.cors_headers("http://127.0.0.1")
    assert h.get("Access-Control-Allow-Origin") == "http://127.0.0.1"


def test_cors_rejects_unknown_origin(monkeypatch):
    monkeypatch.delenv("A2A_CORS_ORIGINS", raising=False)
    import auth
    h = auth.cors_headers("https://evil.example.com")
    assert "Access-Control-Allow-Origin" not in h
    assert h.get("Vary") == "Origin"


def test_cors_custom_allowlist(monkeypatch):
    monkeypatch.setenv("A2A_CORS_ORIGINS", "https://a.example,https://b.example")
    import auth
    assert auth.cors_headers("https://a.example").get("Access-Control-Allow-Origin") == "https://a.example"
    assert "Access-Control-Allow-Origin" not in auth.cors_headers("https://c.example")


def test_is_public_path():
    import auth
    assert auth.is_public_path("/health")
    assert auth.is_public_path("/a2a/.well-known/agent-card.json")
    assert not auth.is_public_path("/a2a/tasks")
    assert not auth.is_public_path("/a2a/tasks/abc")
