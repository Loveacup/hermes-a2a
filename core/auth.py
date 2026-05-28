"""Token authentication and CORS allowlist for hermes-a2a HTTP server.

Provides lightweight service-to-service auth using bearer tokens, plus a CORS
origin allowlist. Solves P0-1 (CORS=* + no auth) for the localhost-only A2A
HTTP/JSON-RPC server.

Design:
    - stdlib only (no third-party deps)
    - Token resolution priority: env var A2A_AUTH_TOKEN > file ~/.hermes/.a2a-token > auto-generate
    - Auto-generated token file is created with mode 0600
    - CORS allowlist via env var A2A_CORS_ORIGINS (comma-separated)
    - Public endpoints (no auth): /health, /a2a/.well-known/agent-card.json
    - Protected endpoints: anything under /a2a/tasks
    - Bearer token comparison uses hmac.compare_digest (constant-time)

Public API:
    load_or_create_token(hermes_home) -> str
    check_auth(headers, path) -> tuple[bool, str]
    cors_headers(origin) -> dict[str, str]
    is_public_path(path) -> bool
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Mapping

logger = logging.getLogger("hermes-a2a.auth")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOKEN_FILE_NAME: str = ".a2a-token"
_TOKEN_ENV_VAR: str = "A2A_AUTH_TOKEN"
_CORS_ENV_VAR: str = "A2A_CORS_ORIGINS"
_DEFAULT_CORS_ORIGINS: str = "http://127.0.0.1,http://localhost"
_TOKEN_BYTES: int = 32  # secrets.token_urlsafe(32) -> ~43 char URL-safe token

# Public endpoints that bypass auth entirely.
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/a2a/.well-known/agent-card.json",
    }
)

# Protected path prefixes that require a valid bearer token.
_PROTECTED_PREFIXES: tuple[str, ...] = ("/a2a/tasks",)

# Module-level cache of the active token. Populated by load_or_create_token().
_active_token: str | None = None


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


def load_or_create_token(hermes_home: str) -> str:
    """Resolve the active auth token.

    Priority:
        1. Environment variable ``A2A_AUTH_TOKEN`` (if non-empty)
        2. File ``<hermes_home>/.a2a-token`` (if exists and non-empty)
        3. Auto-generate a new 32-byte URL-safe token, persist with mode 0600

    The resolved token is cached at module level for subsequent ``check_auth``
    calls.

    Args:
        hermes_home: Directory holding Hermes runtime state (e.g. ``~/.hermes``).

    Returns:
        The active token string.
    """
    global _active_token

    # 1. Env var wins
    env_token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if env_token:
        _active_token = env_token
        logger.info("auth: token loaded from env var %s", _TOKEN_ENV_VAR)
        return _active_token

    # 2. Token file
    home_path = Path(hermes_home).expanduser()
    token_path = home_path / _TOKEN_FILE_NAME

    if token_path.exists():
        try:
            content = token_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning("auth: failed to read token file %s: %s", token_path, e)
            content = ""
        if content:
            _active_token = content
            logger.info("auth: token loaded from %s", token_path)
            return _active_token

    # 3. Generate and persist
    new_token = secrets.token_urlsafe(_TOKEN_BYTES)
    try:
        home_path.mkdir(parents=True, exist_ok=True)
        # Open with restrictive mode from the start to avoid a window where
        # the file is world-readable.
        fd = os.open(
            str(token_path),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, new_token.encode("utf-8"))
        finally:
            os.close(fd)
        # Enforce 0600 even if umask interfered.
        try:
            os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        logger.info("auth: generated new token at %s (mode 0600)", token_path)
    except OSError as e:
        logger.error("auth: failed to persist token to %s: %s", token_path, e)
        # Still return the generated token so the server can run; it just
        # won't be remembered across restarts.

    _active_token = new_token
    return _active_token


def _get_active_token() -> str | None:
    """Return the cached token, loading from env as a fallback.

    Allows ``check_auth`` to function even if ``load_or_create_token`` was
    not explicitly called. The file-based fallback is intentionally skipped
    here because the hermes_home path is unknown to this helper.
    """
    global _active_token
    if _active_token:
        return _active_token
    env_token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if env_token:
        _active_token = env_token
        return _active_token
    return None


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


def is_public_path(path: str) -> bool:
    """Return True if ``path`` is a public endpoint that bypasses auth.

    Only the exact path (query string and fragment stripped) is matched
    against the public set.
    """
    if not path:
        return False
    clean = path.split("?", 1)[0].split("#", 1)[0]
    return clean in _PUBLIC_PATHS


def _is_protected_path(path: str) -> bool:
    """Return True if ``path`` matches a protected prefix."""
    if not path:
        return False
    clean = path.split("?", 1)[0].split("#", 1)[0]
    return any(clean == p or clean.startswith(p + "/") or clean.startswith(p) for p in _PROTECTED_PREFIXES)


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------


def check_auth(headers: Mapping[str, str], path: str) -> tuple[bool, str]:
    """Verify whether a request is authorized.

    Args:
        headers: Mapping of HTTP headers (e.g. ``BaseHTTPRequestHandler.headers``).
            Lookup is case-insensitive for stdlib ``http.client.HTTPMessage``.
        path: Request path (may include query string).

    Returns:
        Tuple ``(allowed, reason)``. When ``allowed`` is False, ``reason``
        contains a short human-readable explanation suitable for logging
        or inclusion in a 401/403 response body.
    """
    # Public paths always pass.
    if is_public_path(path):
        return True, ""

    expected = _get_active_token()
    if not expected:
        # Misconfiguration: no token available. Fail closed.
        return False, "server token not configured"

    try:
        auth_header = headers.get("Authorization", "") or ""
    except AttributeError:
        return False, "missing Authorization header"

    if not auth_header:
        return False, "missing Authorization header"

    parts = auth_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False, "Authorization scheme must be Bearer"

    provided = parts[1].strip()
    if not provided:
        return False, "empty bearer token"

    # Constant-time comparison to prevent timing attacks.
    if hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return True, ""

    return False, "invalid token"


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def _allowed_origins() -> list[str]:
    """Parse the allowed-origins env var into a list (trailing slashes stripped)."""
    raw = os.environ.get(_CORS_ENV_VAR, _DEFAULT_CORS_ORIGINS)
    origins: list[str] = []
    for piece in raw.split(","):
        o = piece.strip().rstrip("/")
        if o:
            origins.append(o)
    return origins


def cors_headers(origin: str | None) -> dict[str, str]:
    """Return CORS response headers for a given request ``Origin``.

    If ``origin`` is in the allowlist, returns headers that echo it back
    along with sane defaults for methods/headers/credentials. If ``origin``
    is None or not allowed, returns only the safe ``Vary: Origin`` header
    (without ``Access-Control-Allow-Origin``), so the browser will block
    the cross-origin response.

    Note:
        OPTIONS preflight requests should be handled by the server before
        invoking this function; this helper only supplies the headers, not
        the response status.

    Args:
        origin: The ``Origin`` request header value, or None.

    Returns:
        Dict of header name -> value to add to the HTTP response.
    """
    base: dict[str, str] = {"Vary": "Origin"}

    if not origin:
        return base

    normalized = origin.strip().rstrip("/")
    if normalized not in _allowed_origins():
        return base

    base.update(
        {
            "Access-Control-Allow-Origin": normalized,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "600",
        }
    )
    return base


__all__ = [
    "load_or_create_token",
    "check_auth",
    "cors_headers",
    "is_public_path",
]
