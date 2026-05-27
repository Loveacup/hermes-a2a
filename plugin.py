"""hermes-a2a plugin — starts A2A HTTP server when registered by Hermes gateway."""
import atexit, hashlib, logging, os, subprocess, sys
from pathlib import Path

logger = logging.getLogger("hermes-a2a")
PLUGIN_NAME, PLUGIN_VERSION = "hermes-a2a", "0.1.0"
PORT_BASE, PORT_RANGE = 8650, 300
_server_proc = None


def _stable_port(profile: str) -> int:
    # sha256 keeps `hash(profile) % 300 + 8650` shape but is deterministic across
    # gateway restarts — Python's built-in hash() is PYTHONHASHSEED-randomized.
    return PORT_BASE + int(hashlib.sha256(profile.encode()).hexdigest(), 16) % PORT_RANGE


def _resolve_profile() -> str:
    # `hermes -p X` sets HERMES_HOME=.../profiles/X/ but does NOT set
    # HERMES_PROFILE inside the gateway process. Derive from HERMES_HOME.
    val = os.environ.get("HERMES_PROFILE", "").strip()
    if val:
        return val
    home = os.environ.get("HERMES_HOME", "")
    if home and "/profiles/" in home:
        return Path(home).name
    return "default"


def register(ctx) -> None:
    """Hermes plugin entry — spawn A2A HTTP server on profile-specific port."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        logger.warning("[hermes-a2a] already running pid=%s; skip spawn", _server_proc.pid)
        return

    profile = _resolve_profile()
    port = _stable_port(profile)
    host = "127.0.0.1"
    hermes_home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")

    env = os.environ.copy()
    env.update(
        A2A_HOST=host,
        A2A_PORT=str(port),
        HERMES_HOME=hermes_home,
        HERMES_PROFILE=profile,  # so server.py reports the right profile in /health
    )

    server_py = Path(__file__).parent / "server.py"
    _server_proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info(
        "[hermes-a2a] v%s listening http://%s:%d (profile=%s, pid=%s)",
        PLUGIN_VERSION, host, port, profile, _server_proc.pid,
    )
    atexit.register(_cleanup)


def _cleanup() -> None:
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        logger.info("[hermes-a2a] terminating server pid=%s", _server_proc.pid)
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
