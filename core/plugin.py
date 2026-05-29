"""hermes-a2a plugin — starts A2A HTTP server when registered by Hermes gateway.

Port assignment uses sha256(profile) % PORT_RANGE + PORT_BASE for stability
across gateway restarts.  Collisions are detected at boot:
  - If `A2A_PORT` is explicitly set in env, it wins (escape hatch).
  - Otherwise we try the stable port and, on collision (EADDRINUSE), scan
    forward through PORT_RANGE for the next free slot, logging a warning.
Logs from the spawned server.py go to ``~/.hermes/logs/a2a-<profile>.log``
instead of /dev/null (P0-3).

BUG-006 (zombie cleanup): before spawning we consult the shared runtime
registry (~/.hermes/a2a-registry.json) and kill any prior server.py owned
by the same profile, preventing accumulation across gateway restarts.
"""
import atexit
import hashlib
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    # Package-style import (e.g. hermes_a2a.plugin).
    from . import registry as _registry  # type: ignore
except (ImportError, ValueError):
    # Flat-deploy fallback: plugin.py loaded with its own dir on sys.path.
    sys.path.insert(0, str(Path(__file__).parent))
    import registry as _registry  # type: ignore

logger = logging.getLogger("hermes-a2a")
PLUGIN_NAME, PLUGIN_VERSION = "hermes-a2a", "0.2.0"
PORT_BASE, PORT_RANGE = 8650, 300
_server_proc = None
_log_handle = None  # keeps file descriptor alive for the lifetime of the proc


def _stable_port(profile: str) -> int:
    return PORT_BASE + int(hashlib.sha256(profile.encode()).hexdigest(), 16) % PORT_RANGE


def _port_free(host: str, port: int) -> bool:
    """Best-effort probe — bind, then release.  Subject to a TOCTOU window
    between probe-release and the child's bind.  The post-spawn health check
    in _wait_for_server() catches losses of that race.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _wait_for_server(host: str, port: int, proc: subprocess.Popen,
                     profile: str, timeout: float = 2.0) -> None:
    """Verify the spawned server.py actually bound ``port`` within ``timeout``.

    Logs a clear warning (does not raise) if:
        - the child exited during startup, or
        - the port stays free past the deadline.

    We deliberately swallow these into log warnings rather than raising:
    Hermes plugin ``register()`` failures cascade into gateway-boot crashes,
    which is a worse outcome than degraded discoverability.  Operators see
    the log + an unreachable port and can investigate.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            logger.error(
                "[hermes-a2a] server.py for profile=%s exited rc=%s during startup "
                "(port %s:%d); see ~/.hermes/logs/a2a-%s.log",
                profile, rc, host, port, profile,
            )
            return
        if not _port_free(host, port):
            return  # child is listening
        time.sleep(0.05)
    logger.warning(
        "[hermes-a2a] server.py for profile=%s pid=%s alive but port %s:%d "
        "still free after %.1fs — possible bind failure",
        profile, proc.pid, host, port, timeout,
    )


def _resolve_port(profile: str, host: str) -> int:
    """Resolve port: env override > stable hash > scan-forward on collision."""
    env_port = os.environ.get("A2A_PORT", "").strip()
    if env_port:
        return int(env_port)

    candidate = _stable_port(profile)
    if _port_free(host, candidate):
        return candidate

    base = candidate
    for offset in range(1, PORT_RANGE):
        p = PORT_BASE + (base - PORT_BASE + offset) % PORT_RANGE
        if _port_free(host, p):
            logger.warning(
                "[hermes-a2a] port %d busy for profile=%s, scanning → using %d",
                candidate, profile, p,
            )
            return p
    raise RuntimeError(
        f"hermes-a2a: no free port in [{PORT_BASE}, {PORT_BASE+PORT_RANGE}) "
        f"for profile={profile}"
    )


def _resolve_profile() -> str:
    val = os.environ.get("HERMES_PROFILE", "").strip()
    if val:
        return val
    home = os.environ.get("HERMES_HOME", "")
    if home and "/profiles/" in home:
        return Path(home).name
    return "default"


def _open_log(profile: str, hermes_home: str):
    """Open ~/.hermes/logs/a2a-<profile>.log for append. Returns file object or DEVNULL."""
    log_dir = Path(hermes_home) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(log_dir / f"a2a-{profile}.log", "ab", buffering=0)
    except OSError as e:
        logger.warning("[hermes-a2a] cannot open log file (%s); falling back to DEVNULL", e)
        return subprocess.DEVNULL


def register(ctx) -> None:
    """Hermes plugin entry — spawn A2A HTTP server on profile-specific port.

    P0-001: 若端口已有 listener（launchd 管理的 A2A server），跳过 spawn。
    避免 plugin.py Popen 与 launchd KeepAlive 三方互杀导致进程风暴。
    """
    global _server_proc, _log_handle
    if _server_proc and _server_proc.poll() is None:
        logger.warning("[hermes-a2a] already running pid=%s; skip spawn", _server_proc.pid)
        return

    profile = _resolve_profile()
    host = "127.0.0.1"

    # ── P0-001: detect launchd-managed A2A server ──────────────────
    # If the intended port already has a TCP listener, assume launchd
    # (or another supervisor) owns the A2A server for this profile.
    # Spawning a second instance would trigger mutual kill with launchd
    # KeepAlive → process storm (observed: loadavg 28.91, 40+ zombies).
    env_port = os.environ.get("A2A_PORT", "").strip()
    intended = int(env_port) if env_port else _stable_port(profile)
    if not _port_free(host, intended):
        logger.info(
            "[hermes-a2a] port %d already in use for profile=%s; "
            "assuming launchd-managed, skip spawn", intended, profile,
        )
        return

    # BUG-006: kill any prior server.py owned by this profile before we try
    # to grab a port. Without this, gateway restarts leak server.py processes
    # (observed: 17 accumulated zombies). Failure here is non-fatal — we
    # still attempt port allocation (which will scan-forward on collision).
    try:
        n = _registry.kill_stale(profile)
        if n:
            logger.info("[hermes-a2a] reaped %d stale server.py for profile=%s", n, profile)
            # Give the kernel a beat to release the port the dead server held.
            time.sleep(0.3)
    except Exception:
        logger.exception("[hermes-a2a] registry.kill_stale failed for profile=%s", profile)

    port = _resolve_port(profile, host)
    hermes_home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")

    env = os.environ.copy()
    env.update(
        A2A_HOST=host,
        A2A_PORT=str(port),
        HERMES_HOME=hermes_home,
        HERMES_PROFILE=profile,
    )

    server_py = Path(__file__).parent / "server.py"
    _log_handle = _open_log(profile, hermes_home)
    _server_proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        env=env,
        stdout=_log_handle,
        stderr=subprocess.STDOUT,
    )
    logger.info(
        "[hermes-a2a] v%s starting http://%s:%d (profile=%s, pid=%s)",
        PLUGIN_VERSION, host, port, profile, _server_proc.pid,
    )
    # Close the TOCTOU window: confirm the child actually bound the port.
    _wait_for_server(host, port, _server_proc, profile)
    atexit.register(_cleanup)


def _cleanup() -> None:
    global _server_proc, _log_handle
    if _server_proc and _server_proc.poll() is None:
        logger.info("[hermes-a2a] terminating server pid=%s", _server_proc.pid)
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
    if _log_handle and _log_handle is not subprocess.DEVNULL:
        try:
            _log_handle.close()
        except OSError:
            pass
